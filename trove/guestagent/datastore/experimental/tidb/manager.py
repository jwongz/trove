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

from trove.common import instance as ds_instance
from trove.common.notification import EndNotification
from trove.guestagent import backup
from trove.guestagent.common import operating_system
from trove.guestagent.datastore.experimental.tib import service
from trove.guestagent.datastore.experimental.tidb import system
from trove.guestagent.datastore import manager
from trove.guestagent import dbaas
from trove.guestagent import volume


LOG = logging.getLogger(__name__)


class Manager(manager.Manager):

    def __init__(self):
        self.app = service.TiDbApp()
        super(Manager, self).__init__('tidb')

    @property
    def status(self):
        return self.app.status

    @property
    def configuration_manager(self):
        return self.app.configuration_manager

    def do_prepare(self, context, packages, databases, memory_mb, users,
                   device_path, mount_point, backup_info,
                   config_contents, root_password, overrides,
                   cluster_config, snapshot):
        """This is called from prepare in the base class."""
        self.app.install_if_needed(packages)
        self.status.wait_for_database_service_start(
            self.app.state_change_wait_time)
        self.app.stop_db()
        self.app.clear_storage()
        mount_point = system.MONGODB_MOUNT_POINT
        if device_path:
            device = volume.VolumeDevice(device_path)
            # unmount if device is already mounted
            device.unmount_device(device_path)
            device.format()
            if os.path.exists(system.MONGODB_MOUNT_POINT):
                device.migrate_data(mount_point)
            device.mount(mount_point)
            operating_system.chown(mount_point,
                                   system.MONGO_USER, system.MONGO_USER,
                                   as_root=True)

            LOG.debug("Mounted the volume %(path)s as %(mount)s.",
                      {'path': device_path, "mount": mount_point})

        if config_contents:
            # Save resolved configuration template first.
            self.app.configuration_manager.save_configuration(config_contents)

        # Apply guestagent specific configuration changes.
        self.app.apply_initial_guestagent_configuration(
            cluster_config, mount_point)

        if not cluster_config:
            # Create the Trove admin user.
            self.app.secure()

        # Don't start mongos until add_config_servers is invoked,
        # don't start members as they should already be running.
        if not (self.app.is_query_router or self.app.is_cluster_member):
            self.app.start_db(update_db=True)

        if not cluster_config and backup_info:
            self._perform_restore(backup_info, context, mount_point, self.app)
            if service.TiDbAdmin().is_root_enabled():
                self.app.status.report_root(context)

    def restart(self, context):
        LOG.debug("Restarting TiDb.")
        self.app.restart()

    def start_db_with_conf_changes(self, context, config_contents):
        LOG.debug("Starting TiDb with configuration changes.")
        self.app.start_db_with_conf_changes(config_contents)

    def stop_db(self, context, do_not_start_on_reboot=False):
        LOG.debug("Stopping TiDb.")
        self.app.stop_db(do_not_start_on_reboot=do_not_start_on_reboot)

    def is_shard_active(self, context, replica_set_name):
        return self.app.is_shard_active(replica_set_name)
