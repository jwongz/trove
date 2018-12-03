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

from os import path

from trove.guestagent.common import operating_system
from trove.guestagent import pkg

OS_NAME = operating_system.get_os()

TIDB_MOUNT_POINT = "/opt/data"

TIDB_SERVICE = ["tidb", "tikv"]
TIDB_KILL = "sudo kill %s"
FIND_PID = "ps xaco pid,cmd | awk '/ti(db|kv|pd)/ {print $1}'"
TIME_OUT = 1000
RUN_SERVER "%s/bin pd-server \
    --name=%s \
    --client-urls=%a \
    --advertise-client-urls=%s \
    --peer-urls=%s \
    --advertise-peer-urls=%s \
    --data-dir=%s \
    --initial-cluster=%s \
    --config=conf/pd.toml \
    --log-file=%s"

TIDB_USER = {operating_system.REDHAT: "tidb",
              operating_system.DEBIAN: "tidb",
              operating_system.SUSE: "tidb"}[OS_NAME]

PACKAGER = pkg.Package()
