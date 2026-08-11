from __future__ import annotations
import importlib.util, sys, types
from pathlib import Path
from pydantic import BaseModel
from fastapi.routing import APIRoute
ROOT=Path(__file__).resolve().parents[1]
MODEL_NAMES=["IndexingJobListResponse","IndexingJobResponse","IngestResponse","RepositoryBrowserListResponse","RepositoryIndexJobResponse","RepositorySourceTreeResponse","UploadProgressResponse","UploadSessionResponse"]
def _load_router_module():
    backend=types.ModuleType("backend"); backend.__path__=[str(ROOT/"backend")]
    api=types.ModuleType("backend.api"); api.__path__=[str(ROOT/"backend"/"api")]
    v1=types.ModuleType("backend.api.v1"); v1.__path__=[str(ROOT/"backend"/"api"/"v1")]
    schemas=types.ModuleType("backend.schemas")
    for name in MODEL_NAMES: setattr(schemas,name,type(name,(BaseModel,),{}))
    sys.modules.update({"backend":backend,"backend.api":api,"backend.api.v1":v1,"backend.schemas":schemas})
    spec=importlib.util.spec_from_file_location("backend.api.v1.repositories",ROOT/"backend"/"api"/"v1"/"repositories.py")
    module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)
    return module,schemas
def _handler(*args,**kwargs): return None
def test_repository_router_preserves_public_routes():
    module,schemas=_load_router_module()
    router=module.create_repositories_router(list_repository_browser_items_handler=_handler,get_repository_source_tree_handler=_handler,ingest_documents_handler=_handler,ingest_documents_with_project_metadata_handler=_handler,create_upload_handler=_handler,add_upload_manifest_handler=_handler,upload_file_part_handler=_handler,get_upload_handler=_handler,complete_upload_handler=_handler,list_indexing_jobs_handler=_handler,get_indexing_job_handler=_handler,cancel_upload_handler=_handler)
    routes={(next(iter(r.methods)),r.path):r for r in router.routes if isinstance(r,APIRoute)}
    assert set(routes)=={("GET","/v1/repositories"),("GET","/v1/repositories/{source_id}/tree"),("POST","/v1/documents/ingest"),("POST","/v1/documents/ingest-with-metadata"),("POST","/v1/uploads"),("POST","/v1/uploads/{upload_id}/manifest"),("PUT","/v1/uploads/{upload_id}/files/{file_id}/parts/{part_number}"),("GET","/v1/uploads/{upload_id}"),("POST","/v1/uploads/{upload_id}/complete"),("GET","/v1/indexing-jobs"),("GET","/v1/indexing-jobs/{job_id}"),("DELETE","/v1/uploads/{upload_id}")}
    assert routes[("POST","/v1/uploads")].status_code==201
    assert routes[("POST","/v1/uploads/{upload_id}/complete")].status_code==202
    assert routes[("DELETE","/v1/uploads/{upload_id}")].status_code==204
    assert routes[("GET","/v1/repositories")].response_model is schemas.RepositoryBrowserListResponse
    assert routes[("GET","/v1/indexing-jobs")].response_model is schemas.IndexingJobListResponse
    assert routes[("POST","/v1/documents/ingest")].response_model is schemas.IngestResponse
