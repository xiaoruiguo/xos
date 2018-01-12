
# Copyright 2017-present Open Networking Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import json
import hashlib
import os
import shutil
from xosgenx.generator import XOSGenerator

from xosconfig import Config
from multistructlog import create_logger
log = create_logger(Config().get('logging'))

DEFAULT_BASE_DIR="/opt/xos"

class DynamicBuilder(object):
    NOTHING_TO_DO = 0
    SOMETHING_CHANGED = 1

    def __init__(self, base_dir=DEFAULT_BASE_DIR):
        self.services_dir = os.path.join(base_dir, "dynamic_services")
        self.manifest_dir = os.path.join(base_dir, "dynamic_services/manifests")
        self.services_dest_dir = os.path.join(base_dir, "services")
        self.coreapi_dir = os.path.join(base_dir, "coreapi")
        self.protos_dir = os.path.join(base_dir, "coreapi/protos")
        self.app_metadata_dir = os.path.join(base_dir, "xos")

    def pre_validate_file(self, item):
        # someone might be trying to trick us into writing files outside the designated directory
        if "/" in item.filename:
            raise Exception("illegal character in filename %s" % item.filename)

    def pre_validate_models(self, request):
        # do whatever validation we can before saving the files
        for item in request.xprotos:
            self.pre_validate_file(item)

        for item in request.decls:
            self.pre_validate_file(item)

        for item in request.attics:
            self.pre_validate_file(item)

    def handle_loadmodels_request(self, request):
        manifest_fn = os.path.join(self.manifest_dir, request.name + ".json")
        if os.path.exists(manifest_fn):
            try:
                manifest = json.loads(open(manifest_fn).read())
            except:
                log.exception("Error loading old manifest", filename=manifest_fn)
                manifest = {}
        else:
            manifest = {}

        # TODO: Check version number to make sure this is not a downgrade ?

        hash = self.generate_request_hash(request)
        if hash == manifest.get("hash"):
            # The hash of the incoming request is identical to the manifest that we have saved, so this request is a
            # no-op.
            log.info("Models are already up-to-date; skipping dynamic load.", name=request.name)
            return self.NOTHING_TO_DO

        self.pre_validate_models(request)

        manifest = self.save_models(request, hash=hash)

        self.run_xosgenx_service(manifest)

        log.debug("Saving service manifest", name=request.name)
        file(manifest_fn, "w").write(json.dumps(manifest))

        log.info("Finished LoadModels request", name=request.name)

        return self.SOMETHING_CHANGED

        # TODO: schedule a restart

    def generate_request_hash(self, request):
        # TODO: could we hash the request rather than individually hashing the subcomponents of the request?
        m = hashlib.sha1()
        m.update(request.name)
        m.update(request.version)
        for item in request.xprotos:
            m.update(item.filename)
            m.update(item.contents)
        for item in request.decls:
            m.update(item.filename)
            m.update(item.contents)
        for item in request.decls:
            m.update(item.filename)
            m.update(item.contents)
        return m.hexdigest()

    def save_models(self, request, hash=None):
        if not hash:
            hash = self.generate_request_hash(request)

        service_dir = os.path.join(self.services_dir, request.name)
        if not os.path.exists(service_dir):
            os.makedirs(service_dir)

        if not os.path.exists(self.manifest_dir):
            os.makedirs(self.manifest_dir)

        manifest_fn = os.path.join(self.manifest_dir, request.name + ".json")

        # Invariant is that if a manifest file exists, then it accurately reflects that has been stored to disk. Since
        # we're about to potentially overwrite files, destroy the old manifest.
        if os.path.exists(manifest_fn):
            os.remove(manifest_fn)

        # convert the request to a manifest, so we can save it
        service_manifest = {"name": request.name,
                            "version": request.version,
                            "hash": hash,
                            "dir": service_dir,
                            "manifest_fn": manifest_fn,
                            "dest_dir": os.path.join(self.services_dest_dir, request.name),
                            "xprotos": [],
                            "decls": [],
                            "attics": []}

        for item in request.xprotos:
            file(os.path.join(service_dir, item.filename), "w").write(item.contents)
            service_manifest["xprotos"].append({"filename": item.filename})

        for item in request.decls:
            file(os.path.join(service_dir, item.filename), "w").write(item.contents)
            service_manifest["decls"].append({"filename": item.filename})

        if request.attics:
            attic_dir = os.path.join(service_dir, "attic")
            service_manifest["attic_dir"] = attic_dir
            if not os.path.exists(attic_dir):
                os.makedirs(attic_dir)
            for item in request.attics:
                file(os.path.join(attic_dir, item.filename), "w").write(item.contents)
                service_manifest["attics"].append({"filename": item.filename})

        return service_manifest

    def run_xosgenx_service(self, manifest):
        if not os.path.exists(manifest["dest_dir"]):
            os.makedirs(manifest["dest_dir"])

        xproto_filenames = [os.path.join(manifest["dir"], x["filename"]) for x in manifest["xprotos"]]

        class Args:
            pass

        # Generate models
        is_service = manifest["name"] != 'core'

        args = Args()
        args.output = manifest["dest_dir"]
        args.attic = os.path.join(manifest["dir"], 'attic')
        args.files = xproto_filenames

        if is_service:
            args.target = 'service.xtarget'
            args.write_to_file = 'target'
        else:
            args.target = 'django.xtarget'
            args.dest_extension = 'py'
            args.write_to_file = 'model'

        XOSGenerator.generate(args)

        # Generate security checks
        class SecurityArgs:
            output = manifest["dest_dir"]
            target = 'django-security.xtarget'
            dest_file = 'security.py'
            write_to_file = 'single'
            files = xproto_filenames

        XOSGenerator.generate(SecurityArgs())

        # Generate __init__.py
        if manifest["name"] == "core":
            class InitArgs:
                output = manifest["dest_dir"]
                target = 'init.xtarget'
                dest_file = '__init__.py'
                write_to_file = 'single'
                files = xproto_filenames

            XOSGenerator.generate(InitArgs())

        else:
            init_py_filename = os.path.join(manifest["dest_dir"], "__init__.py")
            if not os.path.exists(init_py_filename):
                open(init_py_filename, "w").write("# created by dynamicbuild")

        # the xosgenx templates don't handle copying the models.py file for us, so do it here.
        for item in manifest["decls"]:
            src_fn = os.path.join(manifest["dir"], item["filename"])
            dest_fn = os.path.join(manifest["dest_dir"], item["filename"])
            shutil.copyfile(src_fn, dest_fn)

        # If the attic has a header.py, make sure it is copied to the right place
        attic_header_py_src = os.path.join(manifest["dir"], "attic", "header.py")
        service_header_py_dest = os.path.join(manifest["dest_dir"], "header.py")
        if os.path.exists(attic_header_py_src):
            shutil.copyfile(attic_header_py_src, service_header_py_dest)
        elif os.path.exists(service_header_py_dest):
            os.remove(service_header_py_dest)