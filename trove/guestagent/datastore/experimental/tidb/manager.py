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
        mount_point = system.TIDB_MOUNT_POINT
        if device_path:
            device = volume.VolumeDevice(device_path)
            # unmount if device is already mounted
            device.unmount_device(device_path)
            device.format()
            if os.path.exists(system.TIDB_MOUNT_POINT):
                device.migrate_data(mount_point)
            device.mount(mount_point)

            LOG.debug("Mounted the volume %(path)s as %(mount)s.",
                      {'path': device_path, "mount": mount_point})

        # Apply guestagent specific configuration changes.
        self.app.apply_initial_guestagent_configuration(
            cluster_config, mount_point)

    def restart(self, context):
        LOG.debug("Restarting TiDb.")
        self.app.restart()

    def start_db_with_conf_changes(self, context, config_contents):
        LOG.debug("Starting TiDb with configuration changes.")
        self.app.start_db_with_conf_changes(config_contents)

    def stop_db(self, context, do_not_start_on_reboot=False):
        LOG.debug("Stopping TiDb.")
        self.app.stop_db(do_not_start_on_reboot=do_not_start_on_reboot)
