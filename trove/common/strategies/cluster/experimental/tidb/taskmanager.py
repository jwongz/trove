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

from eventlet.timeout import Timeout
from oslo_log import log as logging

from trove.common import cfg
from trove.common.exception import PollTimeOut
from trove.common.instance import ServiceStatuses
from trove.common.strategies.cluster import base
from trove.common import utils
from trove.instance import models
from trove.instance.models import DBInstance
from trove.instance.models import Instance
from trove.instance import tasks as inst_tasks
from trove.taskmanager import api as task_api
import trove.taskmanager.models as task_models


LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class TiDbTaskManagerStrategy(base.BaseTaskManagerStrategy):

    @property
    def task_manager_api_class(self):
        return TiDbTaskManagerAPI

    @property
    def task_manager_cluster_tasks_class(self):
        return TiDbClusterTasks

    @property
    def task_manager_manager_actions(self):

class TiDbClusterTasks(task_models.ClusterTasks):

    def create_cluster(self, context, cluster_id):
        LOG.debug("begin create_cluster for id: %s", cluster_id)

        def _create_cluster():

            # fetch instances by cluster_id against instances table
            db_instances = DBInstance.find_all(cluster_id=cluster_id).all()
            instance_ids = [db_instance.id for db_instance in db_instances]
            LOG.debug("instances in cluster %(cluster_id)s: %(instance_ids)s",
                      {'cluster_id': cluster_id, 'instance_ids': instance_ids})

            if not self._all_instances_ready(instance_ids, cluster_id):
                return

            LOG.debug("all instances in cluster %s ready.", cluster_id)

            instances = [Instance.load(context, instance_id) for instance_id
                         in instance_ids]

            # filter tidb_server in instances into a new list: query_routers
            tidb_server = [instance for instance in instances if
                             instance.type == 'tidb_server']
            LOG.debug("tidb_server: %s",
                      [instance.id for instance in query_routers])
            # filter pd_server in instances into new list: config_servers
            pd_server = [instance for instance in instances if
                              instance.type == 'pd_server']
            LOG.debug("pd_server: %s",
                      [instance.id for instance in pd_server])
            # filter tikv  into a new list: tikvs
            tikv = [instance for instance in instances if
                       instance.type == 'tikv']
            LOG.debug("tikv: %s",
                      [instance.id for instance in tikv])

        cluster_usage_timeout = CONF.cluster_usage_timeout
        timeout = Timeout(cluster_usage_timeout)
        try:
            _create_cluster()
            self.reset_task()
        except Timeout as t:
            if t is not timeout:
                raise  # not my timeout
            LOG.exception("timeout for building cluster.")
            self.update_statuses_on_failure(cluster_id)
        finally:
            timeout.cancel()

        LOG.debug("end create_cluster for id: %s", cluster_id)


    def grow_cluster(self, context, cluster_id, instance_ids):

    def shrink_cluster(self, context, cluster_id, instance_ids):

class TiDbTaskManagerAPI(task_api.API):

    def mongodb_add_shard_cluster(self, cluster_id, shard_id,
                                  replica_set_name):
        LOG.debug("Making async call to add shard cluster %s ", cluster_id)
        version = task_api.API.API_BASE_VERSION
        cctxt = self.client.prepare(version=version)
        cctxt.cast(self.context,
                   "add_shard_cluster",
                   cluster_id=cluster_id,
                   shard_id=shard_id,
                   replica_set_name=replica_set_name)
