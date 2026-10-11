"""Private-project application with durable confirmation of every edit.

The database is the authoritative head, not a shared Workspace. A request
reconstructs an isolated candidate, validates it, then confirms a compare-and-
swap transaction. Failed or superseded candidates are never published.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import uuid

from secure_web import HttpError, Reply, Request, session_cookie


class ProjectGateway:
    def __init__(self, auth, store, runtime_root, *, core=None, secure_cookies=True):
        from server import _load_cadcore
        self.auth, self.store = auth, store
        self.runtime_root = Path(runtime_root).resolve()
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.core = core or _load_cadcore()
        self.secure_cookies = secure_cookies
        catalog = self.core["i18n_catalog"]
        if not hasattr(catalog, "CatalogContext"):
            raise RuntimeError("Private service requires isolated CatalogContext support")
        self.catalog_dirs = [str(Path(catalog.__file__).resolve().parents[1] / "data/i18n")]
        if self.runtime_root.is_relative_to(Path(__file__).resolve().parents[3]):
            raise ValueError("Private request files cannot be inside the source repository")

    @classmethod
    def from_root(cls, root, *, secure_cookies=True):
        from access_control import AuthController
        from project_store import ProjectStore, validate_root
        root = Path(root).absolute()
        for part in (root, *root.parents):
            if part.is_symlink():
                raise ValueError("Private service data root cannot contain a symlink")
        source = Path(__file__).resolve().parents[3]
        if root.resolve().is_relative_to(source):
            raise ValueError("Private runtime data must be outside the repository")
        validate_root(root / "projects")
        validate_root(root / "accounts")
        auth, store = None, None
        try:
            auth = AuthController(root / "accounts")
            store = ProjectStore(root / "projects")
            return cls(auth, store, root / "runtime", secure_cookies=secure_cookies)
        except BaseException:
            if store is not None:
                store.close()
            if auth is not None:
                auth.close()
            raise

    def close(self):
        self.auth.close()
        self.store.close()

    def _locale(self, principal):
        return self.auth.get_preferences(principal.user_id)["locale"]

    def _catalog(self, locale):
        return self.core["i18n_catalog"].CatalogContext(locale, dirs=self.catalog_dirs)

    @contextmanager
    def _workspace(self, snapshot, locale):
        from server import Workspace
        context = self._catalog(locale)
        core = dict(self.core, i18n_catalog=context)
        directory = tempfile.mkdtemp(prefix="request-", dir=self.runtime_root)
        failed = True
        try:
            with context.bind():
                workspace = Workspace(core, locale=locale, standard=snapshot["standard"],
                                      out_root=directory)
                workspace.doc = workspace.docmodel.document_from_l1(snapshot["doc"])
                self._validate_document(workspace.doc)
                for text in [*snapshot["undo"], *snapshot["redo"]]:
                    self._validate_document(workspace.docmodel.document_from_l1(text))
                workspace.undo_stack = list(snapshot["undo"])
                workspace.redo_stack = list(snapshot["redo"])
                workspace.standard = snapshot["standard"]
                if workspace.doc.standard_profile != workspace.standard:
                    raise HttpError("invalid_document", "Document standard disagrees with snapshot", 422)
                yield workspace
                failed = False
        finally:
            if failed:
                # Kernel failures can carry evidence inside this directory.
                # Retain it instead of returning an already deleted coordinate.
                sys.stderr.write(f"opencad-request: failed evidence retained at {directory}\n")
            else:
                shutil.rmtree(directory)

    @staticmethod
    def _snapshot(workspace):
        return {"doc": workspace.l1_text(), "undo": list(workspace.undo_stack),
                "redo": list(workspace.redo_stack), "standard": workspace.standard}

    def _initial_snapshot(self, principal, request_id, standard):
        model = self.core["docmodel"]
        doc = model.new_document(unit="mm", standard_profile=standard)
        doc.meta["doc_uuid"] = str(uuid.uuid5(uuid.NAMESPACE_URL,
                                            f"opencad:{principal.user_id}:{request_id}:doc"))
        doc.meta["created_at"] = "1970-01-01T00:00:00+00:00"
        doc.sketches.append({
            "uuid": str(uuid.uuid5(uuid.NAMESPACE_URL,
                                  f"opencad:{principal.user_id}:{request_id}:sketch")),
            "entities": [], "constraints": [], "dimensions": [],
        })
        angle = self.core["i18n_standards"].projection_angle_for(standard)
        doc.projection_angle = angle or None
        return {"doc": model.document_to_l1(doc), "undo": [], "redo": [], "standard": standard}

    @staticmethod
    def _metadata(head):
        return {"project_id": head["project_id"], "revision": head["revision"],
                "sha256": head["sha256"]}

    @staticmethod
    def _fields(body, allowed, required=()):
        if set(body) - set(allowed) or set(required) - set(body):
            raise HttpError("bad_arguments", "Unexpected or missing request fields")

    def _authorized_head(self, request, principal):
        if not request.project_id:
            raise HttpError("project_required", "Choose a project first")
        head = self.store.read_head(request.project_id)
        self.auth.authorize(principal, owner_id=head["owner"])
        return head

    @staticmethod
    def _query(request, allowed):
        if set(request.query) - set(allowed) or any(
                len(values) != 1 for values in request.query.values()):
            raise HttpError("bad_arguments", "Unexpected or repeated query fields")
        return {key: values[0] for key, values in request.query.items()}

    @staticmethod
    def _domain(workspace, route, body=None):
        extra = workspace.doc.extra
        surfaces = "x_industrial_surfaces" in extra
        assembly = "industrial_assembly_v1" in extra
        part = bool(workspace.doc.features) or any(
            sketch.get("entities") for sketch in workspace.doc.sketches)
        incompatible = False
        if route == "/api/surfaces" and body is not None and body.get("action") != "check_seam":
            incompatible = assembly or part
        elif route == "/api/assembly" and body is not None:
            incompatible = surfaces or part
        elif route in {"/api/parametric", "/api/drawing", "/api/render", "/api/mesh",
                       "/api/step", "/api/dxf", "/api/pdf", "/api/clearance"} or route.startswith("/api/drawing/"):
            incompatible = surfaces or assembly
        elif route == "/api/command":
            incompatible = (surfaces or assembly) and body.get("command") not in {"undo", "redo"}
        if incompatible:
            raise HttpError("unsupported_geometry_domain",
                            "Separate part, surface and assembly projects are required; "
                            "this operation cannot consume the complete model", 422)

    @staticmethod
    def _validate_domains(doc):
        domains = sum((
            bool(doc.features) or any(sketch.get("entities") for sketch in doc.sketches),
            "x_industrial_surfaces" in doc.extra,
            "industrial_assembly_v1" in doc.extra,
        ))
        if domains > 1:
            raise HttpError("unsupported_geometry_domain",
                            "Mixed geometry domains cannot be imported as one complete model", 422)

    def _validate_document(self, doc):
        from server import Workspace
        Workspace._validated_standard(doc.standard_profile,
                                      list(self.core["i18n_standards"].load_profiles()))
        problems = self.core["docmodel"].load_blocking_problems(doc)
        if problems:
            raise HttpError("invalid_document", "; ".join(problems[:8]), 422)
        self._validate_domains(doc)
        validators = (
            ("x_industrial_surfaces", "industrial_surfaces"),
            ("industrial_assembly_v1", "industrial_assembly"),
            ("x_industrial_drawing", "industrial_drawing"),
            ("parametric", "industrial_parametric"),
        )
        import importlib
        for namespace, module in validators:
            if namespace in doc.extra:
                validator = importlib.import_module(module).validate_document
                validator(doc)

    @staticmethod
    def _validate_geometry(workspace, revision):
        if workspace.doc.features:
            from solid_commands import validate
            validate(workspace)
        elif "x_industrial_surfaces" in workspace.doc.extra:
            from industrial_surfaces import read_surfaces
            read_surfaces(workspace)
        elif "industrial_assembly_v1" in workspace.doc.extra:
            from industrial_assembly import read_assembly
            read_assembly(workspace, {"action": "state"})
        if "x_industrial_drawing" in workspace.doc.extra:
            from industrial_drawing import read_drawing
            read_drawing(workspace, {"model_version": revision, "include_svg": False})

    def _current(self, head, principal):
        self.auth.authorize(principal, owner_id=head["owner"])
        latest = self.store.read_head(head["project_id"])
        if self._metadata(latest) != self._metadata(head):
            from project_store import RevisionConflict
            raise RevisionConflict(latest["revision"], latest["sha256"])

    def _industrial_read(self, workspace, request, head, principal):
        path = request.path
        metadata = self._metadata(head)
        if path == "/api/surfaces":
            from industrial_surfaces import read_surfaces
            response = read_surfaces(workspace, request.payload if request.method == "POST" else {})
        elif path == "/api/assembly":
            from industrial_assembly import read_assembly
            query = self._query(request, {"action"})
            response = read_assembly(workspace, {"action": query.get("action", "state")})
        elif path == "/api/parametric":
            from industrial_parametric import read_parametric
            self._query(request, set())
            response = read_parametric(workspace)
        elif path == "/api/drawing":
            from industrial_drawing import read_drawing
            self._query(request, set())
            self._domain(workspace, path)
            response = read_drawing(workspace, {"model_version": head["revision"]})
        else:
            from industrial_drawing import export_drawing
            self._query(request, set())
            self._domain(workspace, path)
            exported = export_drawing(workspace, {
                "model_version": head["revision"], "format": path.rsplit("/", 1)[1]})
            self._current(head, principal)
            return Reply(payload=exported.data, content_type=exported.content_type,
                         headers={**exported.headers, "X-OpenCAD-Revision": str(head["revision"]),
                                  "X-OpenCAD-Snapshot-SHA256": head["sha256"]})
        self.auth.authorize(principal, owner_id=head["owner"])
        return Reply(payload={**response, **metadata})

    def _industrial_candidate(self, workspace, path, body, next_revision):
        self._domain(workspace, path, body)
        if path == "/api/surfaces":
            from industrial_surfaces import prepare_surfaces
            candidate = prepare_surfaces(workspace, body)
        elif path == "/api/assembly":
            from industrial_assembly import prepare_assembly
            candidate = prepare_assembly(workspace, body)
        elif path == "/api/parametric":
            from industrial_parametric import prepare_parametric
            candidate = prepare_parametric(workspace, body)
        else:
            from industrial_drawing import prepare_drawing
            candidate = prepare_drawing(workspace, {**body, "model_version": next_revision})
        before = workspace.l1_text()
        if candidate.before_document_sha256 != hashlib.sha256(before.encode()).hexdigest():
            raise HttpError("candidate_changed",
                            "Industrial candidate does not match the authorized snapshot", 422)
        verifier = getattr(candidate, "verify_integrity", None)
        if not callable(verifier):
            raise HttpError("candidate_changed",
                            "Industrial candidate has no measurement integrity verifier", 422)
        verifier()
        doc = (workspace.docmodel.document_from_l1(candidate.doc)
               if isinstance(candidate.doc, str) else candidate.doc)
        self._validate_document(doc)
        text = workspace.docmodel.document_to_l1(doc)
        if workspace.docmodel.document_to_l1(workspace.docmodel.document_from_l1(text)) != text:
            raise HttpError("candidate_changed", "Industrial candidate is not stable across reopen", 422)
        if text != before:
            workspace.undo_stack.append(before)
            workspace.redo_stack.clear()
        workspace.doc = doc
        workspace.standard = doc.standard_profile
        return candidate.response

    def handle(self, request: Request):
        from access_control import AuthError
        from project_store import ProjectStoreError
        from server import ApiError
        from cadcore.errors import CadError
        from industrial_drawing import DrawingWebError
        try:
            return self._handle(request)
        except AuthError as exc:
            return Reply(exc.status, {"ok": False, "error": exc.code, "detail": exc.detail})
        except ProjectStoreError as exc:
            status = getattr(exc, "status", 500)
            code = getattr(exc, "code", "storage_failed")
            payload = {"ok": False, "error": code, "detail": str(exc)}
            if hasattr(exc, "revision"):
                payload.update(revision=exc.revision, sha256=exc.sha256)
            return Reply(status, payload)
        except ApiError as exc:
            return Reply(exc.status if exc.status != 200 else 422, exc.payload())
        except DrawingWebError as exc:
            return Reply(exc.status, {"ok": False, "error": exc.code, "detail": exc.detail})
        except CadError as exc:
            return Reply(422, {"ok": False, "error": exc.reason or "industrial_failed",
                               "detail": str(exc)})

    def _handle(self, request):
        body, path, method = request.payload, request.path, request.method
        if path == "/api/command" and method == "POST":
            if (not isinstance(body.get("command"), str) or not body["command"].strip()
                    or not isinstance(body.get("args", {}), dict)):
                raise HttpError("bad_arguments", "command must be text and args must be an object")
            from server import Workspace
            if body["command"].strip() in (
                    set(Workspace._DOC_PATH_ARGS) | {"export_pdf", "prepare_release"}):
                raise HttpError("read_export_required",
                                "Use the authorized download route, not a temporary export path", 405)
        if path == "/api/auth/login" and method == "POST":
            self._fields(body, {"username", "password"}, {"username", "password"})
            ticket = self.auth.login(body["username"], body["password"])
            return Reply(payload={"ok": True, "user": ticket.principal.as_dict(),
                                  "expires_at": ticket.expires_at}, headers={
                "Set-Cookie": session_cookie(ticket.token, secure=self.secure_cookies)})
        principal = self.auth.authenticate(request.token)
        if path == "/api/auth/me" and method == "GET":
            return Reply(payload={"ok": True, "user": principal.as_dict(),
                                  "preferences": self.auth.get_preferences(principal.user_id)})
        if path == "/api/auth/logout" and method == "POST":
            self._fields(body, set())
            self.auth.logout(request.token)
            return Reply(payload={"ok": True}, headers={
                "Set-Cookie": session_cookie("", secure=self.secure_cookies, clear=True)})
        if path == "/api/projects" and method == "GET":
            return Reply(payload={"ok": True, "projects": self.store.list_projects(principal.user_id)})
        if path == "/api/projects" and method == "POST":
            self._fields(body, {"title", "request_id", "standard"},
                         {"title", "request_id"})
            standard = body.get("standard", "gbt")
            from server import Workspace
            Workspace._validated_standard(standard, list(self.core["i18n_standards"].load_profiles()))
            snapshot = self._initial_snapshot(principal, body["request_id"], standard)
            head = self.store.create_project(principal.user_id, title=body["title"],
                                             request_id=body["request_id"], payload=snapshot)
            return Reply(201, {"ok": True, **self._metadata(head), "title": head["title"]})
        if path == "/api/locale" and method == "POST":
            self._fields(body, {"locale"}, {"locale"})
            old = self._locale(principal)
            try:
                context = self._catalog(body["locale"])
            except self.core["i18n_catalog"].CatalogError as exc:
                raise HttpError("locale_unavailable", "Requested language is unavailable") from exc
            preferences = self.auth.set_preferences(principal.user_id, locale=context.locale)
            with context.bind():
                strings = dict(context.active_catalog().strings)
            return Reply(payload={"ok": True, **preferences, "changed": int(old != context.locale),
                                  "strings": strings})
        if path == "/api/catalogs" and method == "GET":
            context = self._catalog(self._locale(principal))
            return Reply(payload={"ok": True, "catalogs": [
                {"code": entry.code, "native_name": entry.native_name, "valid": entry.valid}
                for entry in context.scan_catalogs(context.default_dirs())]})
        if path.startswith("/api/release/"):
            if not principal.admin:
                raise HttpError("admin_required", "Maintenance requires an administrator", 403)
            raise HttpError("maintenance_not_enabled", "Use the reviewed deployment procedure", 503)
        if path == "/api/open" and method == "GET":
            raise HttpError("method_not_allowed", "Opening a drawing requires a versioned POST", 405)
        head = self._authorized_head(request, principal)
        locale = self._locale(principal)
        if method == "GET":
            with self._workspace(head["snapshot"], locale) as workspace:
                metadata = self._metadata(head)
                if path in {"/api/surfaces", "/api/assembly", "/api/parametric", "/api/drawing",
                            "/api/drawing/svg", "/api/drawing/pdf", "/api/drawing/dxf"}:
                    return self._industrial_read(workspace, request, head, principal)
                if path == "/api/state":
                    return Reply(payload={**workspace.state(), **metadata,
                                          "undo_depth": len(workspace.undo_stack),
                                          "redo_depth": len(workspace.redo_stack)})
                if path == "/api/mesh":
                    self._domain(workspace, path)
                    return Reply(payload={**workspace.mesh(), **metadata})
                if path == "/api/catalogs":
                    return Reply(payload=workspace.catalogs())
                if path in {"/api/step", "/api/document", "/api/dxf", "/api/pdf"}:
                    if path != "/api/document":
                        self._domain(workspace, path)
                    if path == "/api/step":
                        data, media = workspace.step_bytes(), "application/step"
                    elif path == "/api/document":
                        data, media = workspace.l1_text().encode(), "application/json"
                    elif path == "/api/dxf":
                        target = workspace.export_dxf("drawing.dxf")["path"]
                        data, media = Path(target).read_bytes(), "application/dxf"
                    else:
                        target = workspace.export_pdf("drawing.pdf")["path"]
                        data, media = Path(target).read_bytes(), "application/pdf"
                    self._current(head, principal)
                    return Reply(payload=data, content_type=media, headers={
                        "X-OpenCAD-Revision": str(head["revision"]),
                        "X-OpenCAD-Document-SHA256": hashlib.sha256(
                            workspace.l1_text().encode()).hexdigest()})
            raise HttpError("not_found", "Unknown API route", 404)
        if method != "POST":
            raise HttpError("method_not_allowed", "Use GET or POST", 405)
        if path == "/api/render":
            from industrial_render import handler, bind_snapshot
            with self._workspace(head["snapshot"], locale) as workspace:
                self._domain(workspace, path)
                result = handler(bind_snapshot(workspace, head), body)
                self.auth.authorize(principal, owner_id=head["owner"])
                return result
        if path == "/api/surfaces" and body.get("action") == "check_seam":
            with self._workspace(head["snapshot"], locale) as workspace:
                return self._industrial_read(workspace, request, head, principal)
        if path == "/api/clearance":
            with self._workspace(head["snapshot"], locale) as workspace:
                self._domain(workspace, path)
                return Reply(payload={**workspace.clearance(body), **self._metadata(head)})
        if path == "/api/command" and body.get("command") in {
                "snap_query", "precision_pick", "precision_status", "grip_positions",
                "boundary_probe", "layer_state", "layer_visible_entities",
                "measure_area", "measure_perimeter", "measure_distance"}:
            self._fields(body, {"command", "args"}, {"command"})
            with self._workspace(head["snapshot"], locale) as workspace:
                self._domain(workspace, path, body)
                before = self._snapshot(workspace)
                response = workspace.command(body["command"], body.get("args", {}))
                if self._snapshot(workspace) != before:
                    raise RuntimeError("Declared read-only command changed the workspace")
                return Reply(payload={**response, **self._metadata(head)})
        return self._mutate(request, principal, head, locale)

    def _mutate(self, request, principal, head, locale):
        body, path = request.payload, request.path
        base_fields = {"expected_revision", "expected_sha", "request_id"}
        allowed = {
            "/api/command": ({"command", "args"}, {"command"}),
            "/api/standard": ({"standard"}, {"standard"}),
            "/api/open": ({"doc"}, {"doc"}),
            "/api/save": (set(), set()),
            "/api/surfaces": ({"recipe"}, {"recipe"}),
            "/api/assembly": ({"action", "part", "component", "mate", "component_id",
                               "mate_id", "assembly_json"}, {"action"}),
            "/api/parametric": ({"action", "expected_document_sha256", "configuration",
                                 "overrides", "dimensions"},
                                {"action", "expected_document_sha256"}),
            "/api/drawing": ({"definition"}, {"definition"}),
        }
        if path not in allowed:
            raise HttpError("not_found", "Unknown editing API route", 404)
        fields, required = allowed[path]
        self._fields(body, fields | base_fields, required | base_fields)
        request_payload = {**body, "route": path}
        receipt = self.store.read_receipt(head["project_id"], body["request_id"],
                                          request_payload=request_payload)
        if receipt is not None:
            return Reply(payload={**receipt["response"], **self._metadata(receipt)})
        from project_store import RevisionConflict
        if (body["expected_revision"], body["expected_sha"]) != (head["revision"], head["sha256"]):
            raise RevisionConflict(head["revision"], head["sha256"])
        with self._workspace(head["snapshot"], locale) as workspace:
            if path in {"/api/surfaces", "/api/assembly", "/api/parametric", "/api/drawing"}:
                inner = {key: value for key, value in body.items() if key not in base_fields}
                response = self._industrial_candidate(
                    workspace, path, inner, head["revision"] + 1)
            elif path == "/api/command":
                self._domain(workspace, path, body)
                response = workspace.command(body["command"], body.get("args", {}))
                # Undo/redo restores document bytes, including the standard.
                # The legacy workspace's separate standard field is not an
                # independent authority in the durable service.
                workspace.standard = workspace.doc.standard_profile
            elif path == "/api/standard":
                before = workspace.l1_text()
                response = workspace.set_standard(body["standard"])
                workspace.undo_stack.append(before)
                workspace.redo_stack.clear()
            elif path == "/api/open":
                if not isinstance(body["doc"], str):
                    raise HttpError("bad_arguments", "doc must be L1 JSON text")
                try:
                    candidate = workspace.docmodel.document_from_l1(body["doc"])
                except workspace.docmodel.DocumentError as exc:
                    raise HttpError("invalid_document", "Document schema is invalid", 422) from exc
                self._validate_document(candidate)
                workspace._validated_standard(candidate.standard_profile,
                                               list(workspace.standards.load_profiles()))
                workspace.doc = candidate
                workspace.standard = candidate.standard_profile
                workspace.undo_stack.clear()
                workspace.redo_stack.clear()
                response = {"ok": True, "doc": workspace.l1_text(),
                            "entities": workspace.entity_count()}
            else:
                response = {"ok": True, "saved": True}
            if response.get("ok") is not True:
                raise HttpError("edit_failed", "The edit was not accepted", 422)
            snapshot = self._snapshot(workspace)
            # Revalidate all persisted history too; corrupt undo bytes must not
            # become an acknowledged future edit.
            for text in [snapshot["doc"], *snapshot["undo"], *snapshot["redo"]]:
                doc = workspace.docmodel.document_from_l1(text)
                self._validate_document(doc)
            if path not in {"/api/surfaces", "/api/assembly", "/api/parametric", "/api/drawing"}:
                self._validate_geometry(workspace, head["revision"] + 1)
            self.auth.authorize(principal, owner_id=head["owner"])
            receipt = self.store.commit(
                head["project_id"], expected_revision=body["expected_revision"],
                expected_sha=body["expected_sha"], request_id=body["request_id"],
                request_payload=request_payload, payload=snapshot, response=response)
        return Reply(payload={**receipt["response"], **self._metadata(receipt)})
