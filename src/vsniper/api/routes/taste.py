import io
import logging
import zipfile
from time import perf_counter

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from vsniper.api.routes.users import UserId
from vsniper.core.state import get_state
from vsniper.domain.contracts import (
    ClothingItem,
    JudgmentPromptPreview,
    TasteManualNoteUpdate,
    TasteOfferCreate,
    TasteRecomputeResult,
    TasteSample,
    TasteSampleUpdate,
    TasteSnapshot,
    WardrobeZipImportResult,
    WardrobeZipManifest,
)
from vsniper.integrations.openai.client import OpenAIIntegrationError
from vsniper.integrations.vinted.client import (
    VintedClientError,
    VintedListingUrlError,
)
from vsniper.services.taste_service import TasteRecomputeAlreadyRunning

router = APIRouter(tags=["taste"])
logger = logging.getLogger(__name__)

MAX_IMAGE_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_ZIP_UPLOAD_BYTES = 50 * 1024 * 1024


@router.get("/users/{user_id}/taste", response_model=TasteSnapshot)
def get_taste(user_id: UserId) -> TasteSnapshot:
    return get_state().taste.get_snapshot(user_id)


@router.get("/users/{user_id}/taste/judgment-prompt", response_model=JudgmentPromptPreview)
def get_judgment_prompt(user_id: UserId, clothing_item: ClothingItem) -> JudgmentPromptPreview:
    try:
        return get_state().taste.get_judgment_prompt_preview(user_id, clothing_item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/users/{user_id}/taste/wardrobe", response_model=TasteSample, status_code=status.HTTP_201_CREATED)
async def upload_wardrobe_image(
    user_id: UserId,
    file: UploadFile = File(...),
    clothing_item: ClothingItem = Form(...),
    note: str = Form(default=""),
) -> TasteSample:
    try:
        if not file.filename:
            raise HTTPException(status_code=400, detail="Uploaded image must include a file name.")
        content = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
        if len(content) > MAX_IMAGE_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Uploaded image exceeds the 10 MB limit.")
        return get_state().taste.add_wardrobe_image(
            user_id,
            file_name=file.filename,
            content=content,
            note=note,
            clothing_item=clothing_item,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("/users/{user_id}/taste/wardrobe/import-zip", response_model=WardrobeZipImportResult, status_code=status.HTTP_201_CREATED)
async def import_wardrobe_zip(user_id: UserId, file: UploadFile = File(...)) -> WardrobeZipImportResult:
    try:
        content = await file.read(MAX_ZIP_UPLOAD_BYTES + 1)
        if len(content) > MAX_ZIP_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Uploaded ZIP exceeds the 50 MB limit.")
        if not zipfile.is_zipfile(io.BytesIO(content)):
            raise HTTPException(status_code=400, detail="Uploaded file is not a valid ZIP archive.")
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = set(zf.namelist())
            if "manifest.json" not in names:
                raise HTTPException(status_code=400, detail="ZIP must contain a manifest.json at its root.")
            try:
                manifest = WardrobeZipManifest.model_validate_json(zf.read("manifest.json"))
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=400, detail=f"Invalid manifest.json: {exc}") from exc

            imported: list[TasteSample] = []
            skipped: list[str] = []
            taste = get_state().taste
            for entry in manifest.images:
                if entry.file not in names:
                    skipped.append(f"{entry.file}: not found in ZIP")
                    continue
                try:
                    image_bytes = zf.read(entry.file)
                    sample = taste.add_wardrobe_image(
                        user_id,
                        file_name=entry.file,
                        content=image_bytes,
                        note=entry.note,
                        clothing_item=entry.clothing_item,
                    )
                    imported.append(sample)
                except (ValueError, RuntimeError) as exc:
                    skipped.append(f"{entry.file}: {exc}")
        return WardrobeZipImportResult(imported=imported, skipped=skipped)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        await file.close()


@router.post("/users/{user_id}/taste/offers/from-url", response_model=TasteSample, status_code=status.HTTP_201_CREATED)
def add_offer(user_id: UserId, payload: TasteOfferCreate) -> TasteSample:
    try:
        return get_state().taste.add_offer(user_id, payload)
    except VintedListingUrlError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except VintedClientError as exc:
        status_code = 503 if exc.retryable else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/users/{user_id}/taste/samples/{sample_id}/image")
def get_sample_image(user_id: UserId, sample_id: str) -> FileResponse:
    try:
        path = get_state().taste.sample_image_path(user_id, sample_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Taste sample not found.") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Taste sample image not found.") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return FileResponse(path)


@router.patch("/users/{user_id}/taste/samples/{sample_id}", response_model=TasteSample)
def update_sample(user_id: UserId, sample_id: str, payload: TasteSampleUpdate) -> TasteSample:
    try:
        return get_state().taste.update_sample(user_id, sample_id, payload)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Taste sample not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/users/{user_id}/taste/samples/{sample_id}", response_model=TasteSample)
def delete_sample(user_id: UserId, sample_id: str) -> TasteSample:
    try:
        return get_state().taste.delete_sample(user_id, sample_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Taste sample not found.") from exc


@router.put("/users/{user_id}/taste/manual-note", response_model=TasteSnapshot)
def update_manual_note(user_id: UserId, payload: TasteManualNoteUpdate) -> TasteSnapshot:
    return get_state().taste.update_manual_note(user_id, payload)


@router.post("/users/{user_id}/taste/recompute", response_model=TasteRecomputeResult)
def recompute_taste(user_id: UserId) -> TasteRecomputeResult:
    started = perf_counter()
    logger.info("taste recompute request started")
    try:
        result = get_state().taste.recompute(user_id)
    except TasteRecomputeAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Taste profile recompute is already running. Wait for the current run to finish before starting another.",
        ) from exc
    except OpenAIIntegrationError as exc:
        logger.exception("taste recompute OpenAI integration failed after %.1fs", perf_counter() - started)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception:
        logger.exception("taste recompute request failed after %.1fs", perf_counter() - started)
        raise
    logger.info(
        "taste recompute request finished in %.1fs cost_usd=%.4f input_tokens=%d output_tokens=%d",
        perf_counter() - started,
        result.cost_usd,
        result.input_tokens,
        result.output_tokens,
    )
    return result


@router.post("/users/{user_id}/taste/recompute/cancel", response_model=TasteSnapshot)
def cancel_taste_recompute(user_id: UserId) -> TasteSnapshot:
    logger.info("taste recompute cancel requested")
    return get_state().taste.cancel_recompute(user_id)
