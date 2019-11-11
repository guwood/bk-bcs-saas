# -*- coding: utf-8 -*-
#
# Tencent is pleased to support the open source community by making 蓝鲸智云PaaS平台社区版 (BlueKing PaaS Community Edition) available.
# Copyright (C) 2017-2019 THL A29 Limited, a Tencent company. All rights reserved.
# Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://opensource.org/licenses/MIT
#
# Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
# an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.
#
import json
import datetime
from io import StringIO
from collections import OrderedDict

import jinja2
from ruamel.yaml import YAML
from dataclasses import dataclass
from rest_framework.exceptions import ParseError

from backend.bcs_k8s.app import bcs_info_injector
from backend.bcs_k8s.helm import bcs_variable


@dataclass
class ReleaseData:
    project_id: str
    namespace_info: dict
    show_version: OrderedDict
    template_files: list


class ReleaseDataProcessor:
    def __init__(self, user, raw_release_data):
        self.access_token = user.token.access_token
        self.username = user.username

        self.project_id = raw_release_data.project_id
        self.namespace_info = raw_release_data.namespace_info
        self.show_version = raw_release_data.show_version
        self.template_files = raw_release_data.template_files

    def _parse_yaml(self, yaml_content):
        try:
            yaml = YAML()
            resources = list(yaml.load_all(yaml_content))
        except Exception as e:
            raise ParseError(f'Parse manifest failed: \n{e}\n\nManifest content:\n{yaml_content}')
        else:
            # ordereddict to dict
            return json.loads(json.dumps(resources))

    def _join_manifest(self, resources):
        try:
            yaml = YAML()
            s = StringIO()
            yaml.dump_all(resources, s)
        except Exception as e:
            raise ParseError(f'join manifest failed: {e}')
        else:
            return s.getvalue()

    def _get_bcs_variables(self):
        sys_variables = bcs_variable.collect_system_variable(
            access_token=self.access_token,
            project_id=self.project_id,
            namespace_id=self.namespace_info['id']
        )
        bcs_variables = bcs_variable.get_namespace_variables(self.project_id, self.namespace_info['id'])
        sys_variables.update(bcs_variables)
        return sys_variables

    def _render_with_variables(self, raw_content, bcs_variables):
        t = jinja2.Template(raw_content)
        return t.render(bcs_variables)

    def _set_namespace(self, resources):
        try:
            for res_manifest in resources:
                metadata = res_manifest['metadata']
                metadata['namespace'] = self.namespace_info['name']
        except Exception:
            raise ParseError('set namespace failed: no valid metadata in manifest')

    def _inject_bcs_info(self, yaml_content, inject_configs):
        resources = self._parse_yaml(yaml_content)
        context = {
            'creator': self.username,
            'updator': self.username,
            'version': self.show_version.name
        }
        manager = bcs_info_injector.InjectManager(
            configs=inject_configs,
            resources=resources,
            context=context
        )
        resources = manager.do_inject()
        self._set_namespace(resources)
        return self._join_manifest(resources)

    def _get_inject_configs(self):
        now = datetime.datetime.now()
        configs = bcs_info_injector.inject_configs(
            access_token=self.access_token,
            project_id=self.project_id,
            cluster_id=self.namespace_info['cluster_id'],
            namespace_id=self.namespace_info['id'],
            namespace=self.namespace_info['name'],
            creator=self.username,
            updator=self.username,
            created_at=now,
            updated_at=now,
            version=self.show_version.name,
            source_type='template'
        )
        return configs

    def _inject(self, raw_content, inject_configs, bcs_variables):
        content = self._render_with_variables(raw_content, bcs_variables)
        content = self._inject_bcs_info(content, inject_configs)
        return content

    def release_data(self):
        inject_configs = self._get_inject_configs()
        bcs_variables = self._get_bcs_variables()

        for res_files in self.template_files:
            for f in res_files['files']:
                f['content'] = self._inject(f['content'], inject_configs, bcs_variables)
        return ReleaseData(self.project_id, self.namespace_info, self.show_version, self.template_files)
