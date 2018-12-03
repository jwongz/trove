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

from oslo_log import log as logging

from trove.common import cfg
from trove.common.strategies.cluster import base
from trove.guestagent import api as guest_api


LOG = logging.getLogger(__name__)
CONF = cfg.CONF


class TiDbGuestAgentStrategy(base.BaseGuestAgentStrategy):

    @property
    def guest_client_class(self):
        return TiDbGuestAgentAPI


class TiDbGuestAgentAPI(guest_api.API):
    """Cluster Specific Datastore Guest API

    **** VERSION CONTROLLED API ****

    The methods in this class are subject to version control as
    coordinated by guestagent/api.py.  Whenever a change is made to
    any API method in this class, add a version number and comment
    to the top of guestagent/api.py and use the version number as
    appropriate in this file
    """

    def add_tikv(self, members):
        LOG.debug("Adding members %(members)s on instance %(id)s", {
            'members': members, 'id': self.id})
        version = guest_api.API.API_BASE_VERSION

        return self._call("add_tikv", CONF.tidb.add_members_timeout,
                          version=version, members=members)

    def add_pd_servers(self, pd_servers):
        LOG.debug("Adding pd_servers %(config_servers)s for instance "
                  "%(id)s", {'pd_servers': config_servers,
                             'id': self.id})
        version = guest_api.API.API_BASE_VERSION

        return self._call("add_pd_servers", self.agent_high_timeout,
                          version=version,
                          config_servers=pd_servers)

    def add_tidb_servers(self, tidb_servers):
        LOG.debug("Adding tidb_servers %(config_servers)s for instance "
                  "%(id)s", {'tidb_servers': tidb_servers,
                             'id': self.id})
        version = guest_api.API.API_BASE_VERSION

        return self._call("add_tidb_servers", self.agent_high_timeout,
                          version=version,
                          config_servers=tidb_servers)                     