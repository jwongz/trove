#   Copyright (c) 2014 Mirantis, Inc.
#   All Rights Reserved.
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

import os

from oslo_log import log as logging
from oslo_utils import netutils

from trove.common import cfg
from trove.common import exception
from trove.common.i18n import _
from trove.common import instance as ds_instance
from trove.common.stream_codecs import JsonCodec, SafeYamlCodec
from trove.common import utils
from trove.guestagent.common.configuration import ConfigurationManager
from trove.guestagent.common.configuration import OneFileOverrideStrategy
from trove.guestagent.common import guestagent_utils
from trove.guestagent.common import operating_system
from trove.guestagent.datastore.experimental.mongodb import system
from trove.guestagent.datastore import service


LOG = logging.getLogger(__name__)
CONF = cfg.CONF
CONFIG_FILE = operating_system.file_discovery(system.CONFIG_CANDIDATES)
MANAGER = CONF.datastore_manager if CONF.datastore_manager else 'tidb'

# Configuration group for clustering-related settings.
CNF_CLUSTER = 'clustering'

TIDB_PORT = CONF.tidb.tidb_port
TIKV_PORT = CONF.tidb.tikv_port
PD_PEER_PORT = CONF.tidb.pd_peer_port


class TiDbApp(object):
    """Prepares DBaaS on a Guest container."""

    def __init__(self):
        self.state_change_wait_time = CONF.state_change_wait_time

        revision_dir = guestagent_utils.build_file_path(
            os.path.dirname(CONFIG_FILE),
            ConfigurationManager.DEFAULT_STRATEGY_OVERRIDES_SUB_DIR)
        self.configuration_manager = ConfigurationManager(
            CONFIG_FILE, system.MONGO_USER, system.MONGO_USER,
            SafeYamlCodec(default_flow_style=False),
            requires_root=True,
            override_strategy=OneFileOverrideStrategy(revision_dir))

        self.is_query_router = False
        self.is_cluster_member = False
        self.status = TiDbAppStatus()

    def install_if_needed(self, packages):
        """Prepare the guest machine with a TiDb installation."""
        LOG.info("Preparing Guest as TiDb.")
        if not system.PACKAGER.pkg_is_installed(packages):
            LOG.debug("Installing packages: %s.", str(packages))
            system.PACKAGER.pkg_install(packages, {}, system.TIME_OUT)
        LOG.info("Finished installing TiDb server.")

    def stop_db(self, update_db=False, do_not_start_on_reboot=False):
        self.status.stop_db_service(
            self._get_service_candidates(), self.state_change_wait_time,
            disable_on_boot=do_not_start_on_reboot, update_db=update_db)

    def restart(self):
        self.status.restart_db_service(
            self._get_service_candidates(), self.state_change_wait_time)

    def start_db(self, update_db=False):
        self.status.start_db_service(
            self._get_service_candidates(), self.state_change_wait_time,
            enable_on_boot=True, update_db=update_db)

    def start_db_with_conf_changes(self, config_contents):
        LOG.info('Starting TiDb with configuration changes.')
        if self.status.is_running:
            format = 'Cannot start_db_with_conf_changes because status is %s.'
            LOG.debug(format, self.status)
            raise RuntimeError(format % self.status)
        LOG.info("Initiating config.")
        self.configuration_manager.save_configuration(config_contents)
        # The configuration template has to be updated with
        # guestagent-controlled settings.
        self.apply_initial_guestagent_configuration(
            None, mount_point=system.MONGODB_MOUNT_POINT)
        self.start_db(True)

    def apply_initial_guestagent_configuration(
            self, cluster_config, mount_point=None):
        LOG.debug("Applying initial configuration.")

        # TiDb init scripts assume the PID-file path is writable by the
        # database service.
        self._initialize_writable_run_dir()

        self.configuration_manager.apply_system_override(
            {'processManagement.fork': False,
             'systemLog.destination': 'file',
             'systemLog.logAppend': True
             })

        if mount_point:
            self.configuration_manager.apply_system_override(
                {'storage.dbPath': mount_point})

        if cluster_config is not None:
            self._configure_as_cluster_instance(cluster_config)
        else:
            self._configure_network(TIDB_PORT)

    def _configure_as_cluster_instance(self, cluster_config):
        """Configure this guest as a cluster instance and return its
        new status.
        """
        if cluster_config['instance_type'] == "tidb_server":
            self._configure_as_tidb_server()
        elif cluster_config["instance_type"] == "pd_server":
            self._configure_as_pd_server()
        elif cluster_config["instance_type"] == "tikv":
            self._configure_as_tikv_server(
                cluster_config['replica_set_name'])
        else:
            LOG.error("Bad cluster configuration; instance type "
                      "given as %s.", cluster_config['instance_type'])
            return ds_instance.ServiceStatuses.FAILED

    def _configure_as_tidb_server(self):
        LOG.info("Configuring instance as a cluster query router.")
        self.is_query_router = True

        # FIXME(pmalik): We should really have a separate configuration
        # template for the 'mongos' process.
        # Remove all storage configurations from the template.
        # They apply only to 'mongod' processes.
        # Already applied overrides will be integrated into the base file and
        # their current groups removed.
        config = guestagent_utils.expand_dict(
            self.configuration_manager.parse_configuration())
        if 'storage' in config:
            LOG.debug("Removing 'storage' directives from the configuration "
                      "template.")
            del config['storage']
            self.configuration_manager.save_configuration(
                guestagent_utils.flatten_dict(config))

        # Apply 'mongos' configuration.
        self._configure_network(MONGODB_PORT)
        self.configuration_manager.apply_system_override(
            {'sharding.configDB': ''}, CNF_CLUSTER)

    def _configure_as_pd_server(self):
        LOG.info("Configuring instance as a cluster config server.")
        self._configure_network(CONFIGSVR_PORT)
        self.configuration_manager.apply_system_override(
            {'sharding.clusterRole': 'configsvr'}, CNF_CLUSTER)

    def _configure_as_tikv_server(self, replica_set_name):
        LOG.info("Configuring instance as a cluster member.")
        self.is_cluster_member = True
        self._configure_network(MONGODB_PORT)
        # we don't want these thinking they are in a replica set yet
        # as that would prevent us from creating the admin user,
        # so start mongo before updating the config.
        # mongo will be started by the cluster taskmanager
        self.start_db()
        self.configuration_manager.apply_system_override(
            {'replication.replSetName': replica_set_name}, CNF_CLUSTER)

    def _configure_network(self, port=None):
        """Make the service accessible at a given (or default if not) port.
        """
        instance_ip = netutils.get_my_ipv4()
        bind_interfaces_string = ','.join([instance_ip, '127.0.0.1'])
        options = {'net.bindIp': bind_interfaces_string}
        if port is not None:
            guestagent_utils.update_dict({'net.port': port}, options)

        self.configuration_manager.apply_system_override(options)
        self.status.set_host(instance_ip, port=port)

class TiDBAppStatus(service.BaseDbStatus):

    def __init__(self, host='localhost', port=None):
        super(TiDBAppStatus, self).__init__()
        self.set_host(host, port=port)


class TiDBAdmin(object):
    """Handles administrative tasks on TiDB."""

    # user is cached by making it a class attribute
    admin_user = None

class TiDBClient(object):
    """A wrapper to manage a TiDB connection."""

    # engine information is cached by making it a class attribute
    engine = {}

    def __init__(self, user, host=None, port=None):
        """Get the client. Specifying host and/or port updates cached values.
        :param user: TiDBUser instance used to authenticate
        :param host: server address, defaults to localhost
        :param port: server port, defaults to 27017
        :return:
        """

class TiDbCredentials(object):
    """Handles storing/retrieving credentials. Stored as json in files."""

    def __init__(self, username=None, password=None):
        self.username = username
        self.password = password

    def read(self, filename):
        credentials = operating_system.read_file(filename, codec=JsonCodec())
        self.username = credentials['username']
        self.password = credentials['password']

    def write(self, filename):
        credentials = {'username': self.username,
                       'password': self.password}

        operating_system.write_file(filename, credentials, codec=JsonCodec())
        operating_system.chmod(filename, operating_system.FileMode.SET_USR_RW)
