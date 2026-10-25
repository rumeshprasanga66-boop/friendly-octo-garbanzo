import { useEffect, useId, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Download, FileJson, ImageUp, Loader2, ShieldCheck, UserRound, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useCreatePet, useCreatePetFromPhoto } from "@/hooks/usePets";
import { useT } from "@/i18n";
import { FRAME_SIZES, MAX_FRAMES_PER_STATE, PET_STATES } from "@/lib/petStates";
import { PET_TEMPLATE_URL, PetsRequestError, type Pet } from "@/lib/petsApi";
import { cn } from "@/lib/utils";

/** Mirrors the loader's limits (`dodaios/ui/pets/manifest.py`, docs/pets.md). */
const MAX_NAME = 40;
const MAX_DESCRIPTION = 140;
const MAX_SHEET_BYTES = 2 * 1024 * 1024;
const DEFAULT_FRAME_SIZE = 48;

/** Mirrors the photo route's limits (`dodaios/avatar/portrait.py`). */
const MAX_PHOTO_BYTES = 4 * 1024 * 1024;
const MIN_PHOTO_EDGE = 192;

type Mode = "sheet" | "photo";
type Field = "name" | "description" | "sheet" | "manifest" | "photo";

/**
 * Which field a server refusal belongs to, read from its `detail`. The
 * backend names the part it rejected ("manifest: …", "name must be …"); a
 * detail that names none is about the picture, the part most likely wrong.
 */
export function fieldForDetail(detail: string): Field {
  const text = detail.toLowerCase();
  if (text.includes("manifest") || text.includes("pet.json")) return "manifest";
  if (text.includes("description")) return "description";
  if (text.includes("photo")) return "photo";
  if (/\bname\b/.test(text)) return "name";
  return "sheet";
}

function isPng(file: File): boolean {
  return file.type === "image/png" || file.name.toLowerCase().endsWith(".png");
}

function isJson(file: File): boolean {
  return file.type === "application/json" || file.name.toLowerCase().endsWith(".json");
}

function isPhoto(file: File): boolean {
  return /^image\/(png|jpeg)$/.test(file.type) || /\.(png|jpe?g)$/i.test(file.name);
}

/** Natural width/height of a local image file, or null if it will not decode. */
async function readImageSize(file: File): Promise<{ width: number; height: number } | null> {
  const url = URL.createObjectURL(file);
  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("decode failed"));
      el.src = url;
    });
    return { width: image.naturalWidth, height: image.naturalHeight };
  } catch {
    return null;
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * "Create pet": upload a sprite sheet in the `jarvis-pet/1` layout, name it,
 * and it joins the grid (and becomes the active pet). The checks here are the
 * cheap ones — type and size — so a wrong file is caught before an upload;
 * the backend loader stays the authority and its `detail` lands at the field
 * it is about.
 */
export function CreatePetDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (pet: Pet) => void;
}) {
  const t = useT();
  const create = useCreatePet();
  const createFromPhoto = useCreatePetFromPhoto();
  const formId = useId();
  const sheetInput = useRef<HTMLInputElement>(null);
  const manifestInput = useRef<HTMLInputElement>(null);
  const photoInput = useRef<HTMLInputElement>(null);

  const [mode, setMode] = useState<Mode>("sheet");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [sheet, setSheet] = useState<File | null>(null);
  const [sheetUrl, setSheetUrl] = useState<string | null>(null);
  const [frameSize, setFrameSize] = useState<number>(DEFAULT_FRAME_SIZE);
  const [manifest, setManifest] = useState<File | null>(null);
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoUrl, setPhotoUrl] = useState<string | null>(null);
  const [errors, setErrors] = useState<Partial<Record<Field, string>>>({});

  // A fresh form every time the dialog opens.
  useEffect(() => {
    if (!open) return;
    setMode("sheet");
    setName("");
    setDescription("");
    setSheet(null);
    setFrameSize(DEFAULT_FRAME_SIZE);
    setManifest(null);
    setPhoto(null);
    setErrors({});
  }, [open]);

  // The preview is a local object URL; release it when the file changes.
  useEffect(() => {
    if (!sheet) {
      setSheetUrl(null);
      return;
    }
    const url = URL.createObjectURL(sheet);
    setSheetUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [sheet]);

  useEffect(() => {
    if (!photo) {
      setPhotoUrl(null);
      return;
    }
    const url = URL.createObjectURL(photo);
    setPhotoUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [photo]);

  function pickSheet(file: File | undefined) {
    if (!file) return;
    if (!isPng(file)) {
      setErrors((e) => ({ ...e, sheet: t("pets.create_dialog.error_not_png") }));
      return;
    }
    if (file.size > MAX_SHEET_BYTES) {
      setErrors((e) => ({ ...e, sheet: t("pets.create_dialog.error_too_big") }));
      return;
    }
    setErrors((e) => ({ ...e, sheet: undefined }));
    setSheet(file);
  }

  async function pickPhoto(file: File | undefined) {
    if (!file) return;
    if (!isPhoto(file)) {
      setErrors((e) => ({ ...e, photo: t("pets.create_dialog.photo_error_not_image") }));
      return;
    }
    if (file.size > MAX_PHOTO_BYTES) {
      setErrors((e) => ({ ...e, photo: t("pets.create_dialog.photo_error_too_big") }));
      return;
    }
    // The backend needs at least one 48 px frame after cropping; catching a
    // too-small photo here saves a pointless upload.
    const size = await readImageSize(file);
    if (size && Math.min(size.width, size.height) < MIN_PHOTO_EDGE) {
      setErrors((e) => ({ ...e, photo: t("pets.create_dialog.photo_error_too_small") }));
      return;
    }
    setErrors((e) => ({ ...e, photo: undefined }));
    setPhoto(file);
  }

  function pickManifest(file: File | undefined) {
    if (!file) return;
    if (!isJson(file)) {
      setErrors((e) => ({ ...e, manifest: t("pets.create_dialog.error_manifest") }));
      return;
    }
    setErrors((e) => ({ ...e, manifest: undefined }));
    setManifest(file);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    const next: Partial<Record<Field, string>> = {};
    if (!trimmed) next.name = t("pets.create_dialog.error_name");
    if (mode === "sheet" && !sheet) next.sheet = t("pets.create_dialog.error_no_sheet");
    if (mode === "photo" && !photo) next.photo = t("pets.create_dialog.photo_error_not_image");
    if (next.name || next.sheet || next.photo) {
      setErrors(next);
      return;
    }
    setErrors({});
    try {
      const pet =
        mode === "photo"
          ? await createFromPhoto.mutateAsync({
              photo: photo as File,
              name: trimmed,
              description: description.trim(),
            })
          : await create.mutateAsync({
              sheet: sheet as File,
              name: trimmed,
              description: description.trim(),
              frameSize,
              manifest: manifest ? await manifest.text() : null,
            });
      onCreated(pet);
      onOpenChange(false);
    } catch (error) {
      const message = (error as Error).message;
      if (error instanceof PetsRequestError && (error.status === 400 || error.status === 413)) {
        setErrors({ [fieldForDetail(message)]: message });
      } else {
        setErrors({ [mode === "photo" ? "photo" : "sheet"]: message });
      }
    }
  }

  const busy = create.isPending || createFromPhoto.isPending;
  const errorId = (field: Field) => `${formId}-${field}-error`;
  const fieldError = (field: Field) =>
    errors[field] ? (
      <p id={errorId(field)} role="alert" className="mt-1.5 text-sm text-destructive">
        {errors[field]}
      </p>
    ) : null;

  return (
    <Dialog.Root open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-[80] bg-scrim/75 backdrop-blur-sm data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
        <Dialog.Content
          data-testid="create-pet-dialog"
          className="fixed inset-0 z-[80] m-auto flex h-fit max-h-[min(90dvh,760px)] w-[min(560px,calc(100vw-32px))] flex-col overflow-hidden rounded-2xl border border-border bg-popover text-popover-foreground shadow-float outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none"
        >
          <div className="flex items-start justify-between gap-4 border-b border-border px-6 py-4">
            <div className="min-w-0">
              <Dialog.Title className="text-lg font-semibold text-foreground-strong">
                {t("pets.create_dialog.title")}
              </Dialog.Title>
              <Dialog.Description className="mt-0.5 text-sm text-muted-foreground">
                {t("pets.create_dialog.description")}
              </Dialog.Description>
            </div>
            <Dialog.Close
              disabled={busy}
              aria-label={t("common.close")}
              className="grid h-8 w-8 shrink-0 place-items-center rounded-lg text-muted-foreground hover:bg-secondary hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              <X className="h-4 w-4" aria-hidden />
            </Dialog.Close>
          </div>

          <form
            id={formId}
            noValidate
            onSubmit={(event) => void submit(event)}
            className="min-h-0 flex-1 space-y-5 overflow-y-auto px-6 py-5 scrollbar-jarvis"
          >
            <div
              role="radiogroup"
              aria-label={t("pets.create_dialog.title")}
              data-testid="create-pet-mode"
              className="inline-flex rounded-lg border border-border bg-background p-0.5"
            >
              {(["sheet", "photo"] as const).map((value) => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={mode === value}
                  data-testid={`create-pet-mode-${value}`}
                  onClick={() => setMode(value)}
                  className={cn(
                    "inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    mode === value
                      ? "bg-secondary text-foreground-strong"
                      : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  {value === "sheet" ? (
                    <ImageUp className="h-3.5 w-3.5" aria-hidden />
                  ) : (
                    <UserRound className="h-3.5 w-3.5" aria-hidden />
                  )}
                  {value === "sheet"
                    ? t("pets.create_dialog.sheet_label")
                    : t("pets.create_dialog.photo_title")}
                </button>
              ))}
            </div>

            <div>
              <div className="flex items-baseline justify-between gap-3">
                <label htmlFor={`${formId}-name`} className="text-sm font-medium text-foreground">
                  {t("pets.create_dialog.name_label")}
                </label>
                <span className="text-xs text-foreground-faint">{`${name.length}/${MAX_NAME}`}</span>
              </div>
              <Input
                id={`${formId}-name`}
                data-testid="create-pet-name"
                className="mt-1.5"
                value={name}
                maxLength={MAX_NAME}
                placeholder={t("pets.create_dialog.name_placeholder")}
                aria-invalid={errors.name ? true : undefined}
                aria-describedby={errors.name ? errorId("name") : undefined}
                onChange={(event) => setName(event.target.value)}
              />
              {fieldError("name")}
            </div>

            <div>
              <div className="flex items-baseline justify-between gap-3">
                <label
                  htmlFor={`${formId}-description`}
                  className="text-sm font-medium text-foreground"
                >
                  {t("pets.create_dialog.description_label")}
                </label>
                <span className="text-xs text-foreground-faint">
                  {`${description.length}/${MAX_DESCRIPTION}`}
                </span>
              </div>
              <Input
                id={`${formId}-description`}
                data-testid="create-pet-description"
                className="mt-1.5"
                value={description}
                maxLength={MAX_DESCRIPTION}
                placeholder={t("pets.create_dialog.description_placeholder")}
                aria-invalid={errors.description ? true : undefined}
                aria-describedby={errors.description ? errorId("description") : undefined}
                onChange={(event) => setDescription(event.target.value)}
              />
              {fieldError("description")}
            </div>

            {mode === "photo" && (
              <>
                <div>
                  <p className="text-sm font-medium text-foreground">
                    {t("pets.create_dialog.photo_label")}
                  </p>
                  <div className="mt-1.5 flex items-center gap-4 rounded-lg border border-dashed border-border-strong p-3">
                    <div className="grid h-20 w-20 shrink-0 place-items-center overflow-hidden rounded-md bg-secondary">
                      {photoUrl ? (
                        <img
                          src={photoUrl}
                          alt={t("pets.create_dialog.photo_preview_alt")}
                          className="max-h-full max-w-full object-cover"
                        />
                      ) : (
                        <UserRound className="h-6 w-6 text-muted-foreground" aria-hidden />
                      )}
                    </div>
                    <div className="min-w-0 flex-1">
                      {photo && (
                        <p className="truncate text-sm font-medium text-foreground">{photo.name}</p>
                      )}
                      <p className="text-xs text-muted-foreground">
                        {t("pets.create_dialog.photo_hint")}
                      </p>
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        className="mt-2"
                        onClick={() => photoInput.current?.click()}
                      >
                        {photo
                          ? t("pets.create_dialog.photo_change")
                          : t("pets.create_dialog.photo_choose")}
                      </Button>
                      <input
                        ref={photoInput}
                        type="file"
                        accept="image/png,image/jpeg"
                        className="sr-only"
                        tabIndex={-1}
                        data-testid="create-pet-photo"
                        aria-label={t("pets.create_dialog.photo_label")}
                        onChange={(event) => {
                          void pickPhoto(event.target.files?.[0]);
                          event.target.value = "";
                        }}
                      />
                    </div>
                  </div>
                  {fieldError("photo")}
                </div>

                <div className="rounded-lg bg-secondary/60 p-4">
                  <div className="flex items-center gap-2">
                    <ShieldCheck
                      className="h-4 w-4 shrink-0 text-muted-foreground"
                      aria-hidden
                    />
                    <p className="text-sm font-medium text-foreground">
                      {t("pets.create_dialog.photo_privacy")}
                    </p>
                  </div>
                  <p className="mt-1 text-sm text-muted-foreground">
                    {t("pets.create_dialog.photo_notice")}
                  </p>
                </div>
              </>
            )}
            {mode === "sheet" && (
              <>
              <div>
                <p className="text-sm font-medium text-foreground">
                  {t("pets.create_dialog.sheet_label")}
                </p>
                <div className="mt-1.5 flex items-center gap-4 rounded-lg border border-dashed border-border-strong p-3">
                  <div className="grid h-20 w-20 shrink-0 place-items-center overflow-hidden rounded-md bg-secondary">
                    {sheetUrl ? (
                      <img
                        src={sheetUrl}
                        alt={t("pets.create_dialog.sheet_preview_alt")}
                        className="max-h-full max-w-full object-contain [image-rendering:pixelated]"
                      />
                    ) : (
                      <ImageUp className="h-6 w-6 text-muted-foreground" aria-hidden />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    {sheet && (
                      <p className="truncate text-sm font-medium text-foreground">{sheet.name}</p>
                    )}
                    <p className="text-xs text-muted-foreground">
                      {t("pets.create_dialog.sheet_hint")}
                    </p>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      className="mt-2"
                      onClick={() => sheetInput.current?.click()}
                    >
                      {sheet ? t("pets.create_dialog.sheet_change") : t("pets.create_dialog.sheet_choose")}
                    </Button>
                    <input
                      ref={sheetInput}
                      type="file"
                      accept="image/png"
                      className="sr-only"
                      tabIndex={-1}
                      data-testid="create-pet-sheet"
                      aria-label={t("pets.create_dialog.sheet_label")}
                      onChange={(event) => {
                        pickSheet(event.target.files?.[0]);
                        event.target.value = "";
                      }}
                    />
                  </div>
                </div>
                {fieldError("sheet")}
              </div>

              <div>
                <p id={`${formId}-frame`} className="text-sm font-medium text-foreground">
                  {t("pets.create_dialog.frame_size_label")}
                </p>
                <div
                  role="radiogroup"
                  aria-labelledby={`${formId}-frame`}
                  className="mt-1.5 inline-flex rounded-lg border border-border bg-background p-0.5"
                >
                  {FRAME_SIZES.map((size) => (
                    <button
                      key={size}
                      type="button"
                      role="radio"
                      aria-checked={frameSize === size}
                      data-testid={`create-pet-frame-${size}`}
                      onClick={() => setFrameSize(size)}
                      className={cn(
                        "rounded-md px-3 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                        frameSize === size
                          ? "bg-secondary text-foreground-strong"
                          : "text-muted-foreground hover:text-foreground",
                      )}
                    >
                      {t("pets.create_dialog.frame_size_option").replaceAll("{0}", String(size))}
                    </button>
                  ))}
                </div>
              </div>

              <div className="rounded-lg bg-secondary/60 p-4">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm font-medium text-foreground">
                    {t("pets.create_dialog.layout_title")}
                  </p>
                  <a
                    href={PET_TEMPLATE_URL}
                    download="pet-template.png"
                    data-testid="create-pet-template"
                    className="inline-flex items-center gap-1.5 rounded-md text-sm font-medium text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    <Download className="h-3.5 w-3.5" aria-hidden />
                    {t("pets.create_dialog.template_download")}
                  </a>
                </div>
                <p className="mt-1 text-sm text-muted-foreground">
                  {t("pets.create_dialog.layout_body").replace("{0}", String(MAX_FRAMES_PER_STATE))}
                </p>
                <ol className="mt-3 flex flex-wrap gap-1.5" data-testid="create-pet-rows">
                  {PET_STATES.map((state, row) => (
                    <li
                      key={state}
                      className="flex items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-xs text-foreground"
                    >
                      <span className="font-mono text-foreground-faint">{row + 1}</span>
                      {t(`pets.states.${state}`)}
                    </li>
                  ))}
                </ol>
              </div>
              </>
            )}
            {mode === "sheet" && (
              <>
              <div>
                <p className="text-sm font-medium text-foreground">
                  {t("pets.create_dialog.manifest_label")}
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  {t("pets.create_dialog.manifest_hint")}
                </p>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {manifest ? (
                    <>
                      <span className="inline-flex min-w-0 items-center gap-1.5 rounded-md border border-border bg-background px-2 py-1 text-sm text-foreground">
                        <FileJson className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden />
                        <span className="truncate">{manifest.name}</span>
                      </span>
                      <Button type="button" variant="ghost" size="sm" onClick={() => setManifest(null)}>
                        {t("pets.create_dialog.manifest_remove")}
                      </Button>
                    </>
                  ) : (
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() => manifestInput.current?.click()}
                    >
                      <FileJson aria-hidden />
                      {t("pets.create_dialog.manifest_choose")}
                    </Button>
                  )}
                  <input
                    ref={manifestInput}
                    type="file"
                    accept=".json,application/json"
                    className="sr-only"
                    tabIndex={-1}
                    data-testid="create-pet-manifest"
                    aria-label={t("pets.create_dialog.manifest_label")}
                    onChange={(event) => {
                      pickManifest(event.target.files?.[0]);
                      event.target.value = "";
                    }}
                  />
                </div>
                {fieldError("manifest")}
              </div>
              </>
            )}
          </form>

          <div className="flex justify-end gap-2 border-t border-border px-6 py-4">
            <Button
              type="button"
              variant="ghost"
              disabled={busy}
              onClick={() => onOpenChange(false)}
            >
              {t("common.cancel")}
            </Button>
            <Button type="submit" form={formId} disabled={busy} data-testid="create-pet-submit">
              {busy && <Loader2 className="animate-spin" aria-hidden />}
              {busy
                ? mode === "photo"
                  ? t("pets.create_dialog.photo_creating")
                  : t("pets.create_dialog.creating")
                : mode === "photo"
                  ? t("pets.create_dialog.photo_use")
                  : t("pets.create_dialog.create")}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
