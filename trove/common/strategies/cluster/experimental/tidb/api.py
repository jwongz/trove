# Copyright 2014 eBay Software Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

from novaclient import exceptions as nova_exceptions
from oslo_log import log as logging

from trove.cluster import models
from trove.cluster.tasks import ClusterTasks
from trove.cluster.views import ClusterView
from trove.common import cfg
from trove.common import exception
from trove.common.i18n import _
from trove.common.notification import DBaaSClusterGrow
from trove.common.notification import StartNotification
from trove.common import remote
from trove.common import server_group as srv_grp
from trove.common.strategies.cluster import base
from trove.common import utils
from trove.datastore import models as datastore_models
from trove.extensions.mgmt.clusters.views import MgmtClusterView
from trove.instance import models as inst_models
from trove.quota.quota import check_quotas
from trove.taskmanager import api as task_api


LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class TiDbAPIStrategy(base.BaseAPIStrategy):

    @property
    def cluster_class(self):
        return TiDbCluster

    @property
    def cluster_view_class(self):
        return TiDbClusterView

    @property
    def mgmt_cluster_view_class(self):
        return TiDbMgmtClusterView


class TiDbCluster(models.Cluster):

    @classmethod
    def create(cls, context, name, datastore, datastore_version,
               instances, extended_properties, locality, configuration):

        if configuration:
            raise exception.ConfigurationNotSupported()

        # TODO(amcreynolds): consider moving into CONF and even supporting
        # TODO(amcreynolds): an array of values, e.g. [3, 5, 7]
        # TODO(amcreynolds): or introduce a min/max num_instances and set
        # TODO(amcreynolds): both to 3
        num_instances = len(instances)
        if num_instances != 3:
            raise exception.ClusterNumInstancesNotSupported(num_instances=3)

        tidb_conf = CONF.get(datastore_version.manager)

        num_tidbsvr = int(extended_properties.get(
            'num_tidbsvr', tidb_conf.num_tidb_servers_per_cluster))
        num_pdsvr = int(extended_properties.get(
            'num_pdsvr', tidb_conf.num_pd_servers_per_cluster))

        delta_instances = num_instances + num_tidbsvr + num_pdsvr

        models.validate_instance_flavors(
            context, instances, tidb_conf.volume_support,
            tidb_conf.device_path)
        models.assert_homogeneous_cluster(instances)

        flavor_id = instances[0]['flavor_id']

        volume_size = instances[0].get('volume_size', None)
        volume_type = instances[0].get('volume_type', None)

        nics = instances[0].get('nics', None)

        azs = [instance.get('availability_zone', None)
               for instance in instances]

        regions = [instance.get('region_name', None)
                   for instance in instances]

        db_info = models.DBCluster.create(
            name=name, tenant_id=context.tenant,
            datastore_version_id=datastore_version.id,
            task_status=ClusterTasks.BUILDING_INITIAL)

        replica_set_name = "rs1"

        tikv_config = {"id": db_info.id,
                         "shard_id": utils.generate_uuid(),
                         "instance_type": "tikv",
                         "replica_set_name": replica_set_name}

        tidbsvr_config = {"id": db_info.id,
                            "instance_type": "tidb_server"}

        pdsvr_config = {"id": db_info.id,
                         "instance_type": "pd_server"}

        for i in range(1, pdsvr_config + 1):
            instance_name = "%s-%s-%s" % (name, "tidb", str(i))
            inst_models.Instance.create(context, instance_name,
                                        flavor_id,
                                        datastore_version.image_id,
                                        [], [], datastore,
                                        datastore_version,
                                        volume_size, None,
                                        availability_zone=None,
                                        nics=nics,
                                        configuration_id=None,
                                        cluster_config=pdsvr_config,
                                        volume_type=volume_type,
                                        locality=locality,
                                        region_name=regions[i % num_instances]
                                        )

        for i in range(1, num_tidbsvr + 1):
            instance_name = "%s-%s-%s" % (name, "configsvr", str(i))
            inst_models.Instance.create(context, instance_name,
                                        flavor_id,
                                        datastore_version.image_id,
                                        [], [], datastore,
                                        datastore_version,
                                        volume_size, None,
                                        availability_zone=None,
                                        nics=nics,
                                        configuration_id=None,
                                        cluster_config=tidbsvr_config,
                                        volume_type=volume_type,
                                        locality=locality,
                                        region_name=regions[i % num_instances]
                                        )

        for i in range(0, num_instances):
            instance_name = "%s-%s-%s" % (name, replica_set_name, str(i + 1))
            inst_models.Instance.create(context, instance_name,
                                        flavor_id,
                                        datastore_version.image_id,
                                        [], [], datastore,
                                        datastore_version,
                                        volume_size, None,
                                        availability_zone=azs[i],
                                        nics=nics,
                                        configuration_id=None,
                                        cluster_config=tikv_config,
                                        volume_type=volume_type,
                                        modules=instances[i].get('modules'),
                                        locality=locality,
                                        region_name=regions[i])

        task_api.load(context, datastore_version.manager).create_cluster(
            db_info.id)

        return TiDbCluster(context, db_info, datastore, datastore_version)

    def _parse_grow_item(self, item):
        used_keys = []

        def _check_option(key, required=False, valid_values=None):
            if required and key not in item:
                raise exception.TroveError(
                    _('An instance with the options %(given)s is missing '
                      'the TiDb required option %(expected)s.')
                    % {'given': item.keys(), 'expected': key}
                )
            value = item.get(key, None)
            if valid_values and value not in valid_values:
                raise exception.TroveError(
                    _('The value %(value)s for key %(key)s is invalid. '
                      'Allowed values are %(valid)s.')
                    % {'value': value, 'key': key, 'valid': valid_values}
                )
            used_keys.append(key)
            return value

        flavor_id = utils.get_id_from_href(_check_option('flavorRef',
                                                         required=True))
        volume_size = int(_check_option('volume', required=True)['size'])
        instance_type = _check_option('type', required=True,
                                      valid_values=[u'replica',
                                                    u'query_router'])
        name = _check_option('name')
        related_to = _check_option('related_to')
        nics = _check_option('nics')
        availability_zone = _check_option('availability_zone')

        unused_keys = list(set(item.keys()).difference(set(used_keys)))
        if unused_keys:
            raise exception.TroveError(
                _('The arguments %s are not supported by TiDb.')
                % unused_keys
            )

        instance = {'flavor_id': flavor_id,
                    'volume_size': volume_size,
                    'instance_type': instance_type}
        if name:
            instance['name'] = name
        if related_to:
            instance['related_to'] = related_to
        if nics:
            instance['nics'] = nics
        if availability_zone:
            instance['availability_zone'] = availability_zone
        return instance

    def grow(self, instances):
        """Extend a cluster by adding new instances.
        Currently only supports adding a replica set to the cluster.
        """

    def shrink(self, instance_ids):
        """Removes instances from a cluster.
        Currently only supports removing entire replica sets from the cluster.
        """

class TiDbClusterView(ClusterView):

class TiDbMgmtClusterView(MgmtClusterView):