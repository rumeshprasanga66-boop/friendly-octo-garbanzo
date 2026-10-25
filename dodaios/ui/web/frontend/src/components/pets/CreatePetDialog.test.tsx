import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CreatePetDialog, fieldForDetail } from "@/components/pets/CreatePetDialog";

function png(name = "sheet.png", bytes = 64): File {
  return new File([new Uint8Array(bytes)], name, { type: "image/png" });
}

function renderDialog(onCreated = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CreatePetDialog open onOpenChange={vi.fn()} onCreated={onCreated} />
    </QueryClientProvider>,
  );
  return onCreated;
}

function fill(name: string, file: File) {
  fireEvent.change(screen.getByTestId("create-pet-name"), { target: { value: name } });
  fireEvent.change(screen.getByTestId("create-pet-sheet"), { target: { files: [file] } });
}

beforeEach(() => {
  // jsdom has no object URLs; the preview only needs a string.
  URL.createObjectURL = vi.fn(() => "blob:preview");
  URL.revokeObjectURL = vi.fn();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("CreatePetDialog", () => {
  it("lists the sheet rows in state order", () => {
    renderDialog();
    const rows = screen.getByTestId("create-pet-rows").textContent ?? "";
    expect(rows.indexOf("Idle")).toBeLessThan(rows.indexOf("Listening"));
    expect(rows.indexOf("Error")).toBeLessThan(rows.indexOf("Sleeping"));
  });

  it("offers the empty template sheet as a download", () => {
    renderDialog();
    const link = screen.getByTestId("create-pet-template");
    expect(link.getAttribute("href")).toBe("/api/pets/template.png");
    expect(link.hasAttribute("download")).toBe(true);
    expect(link.textContent).toBe("Download template");
  });

  it("refuses a file that is not a PNG before uploading anything", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderDialog();

    fireEvent.change(screen.getByTestId("create-pet-sheet"), {
      target: { files: [new File(["x"], "sheet.gif", { type: "image/gif" })] },
    });

    expect(screen.getByRole("alert").textContent).toBe("Choose a PNG image.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a sheet over 2 MB", () => {
    renderDialog();
    fireEvent.change(screen.getByTestId("create-pet-sheet"), {
      target: { files: [png("big.png", 2 * 1024 * 1024 + 1)] },
    });
    expect(screen.getByRole("alert").textContent).toBe("This image is larger than 2 MB.");
  });

  it("asks for a name before uploading", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderDialog();
    fireEvent.change(screen.getByTestId("create-pet-sheet"), { target: { files: [png()] } });
    fireEvent.click(screen.getByTestId("create-pet-submit"));
    expect(screen.getByRole("alert").textContent).toBe("Give your pet a name.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("uploads the form and hands the new pet back", async () => {
    const created = {
      id: "u0123456789abcdef",
      name: "Pixel",
      description: "",
      builtin: false,
      frame_size: 32,
      animations: {},
      sheet_url: "/api/pets/u0123456789abcdef/sheet.png",
    };
    const fetchSpy = vi.fn(async () => ({ ok: true, status: 201, json: async () => created }));
    vi.stubGlobal("fetch", fetchSpy);
    const onCreated = renderDialog();

    fill("Pixel", png());
    fireEvent.click(screen.getByTestId("create-pet-frame-32"));
    fireEvent.click(screen.getByTestId("create-pet-submit"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/pets");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect(form.get("name")).toBe("Pixel");
    expect(form.get("frame_size")).toBe("32");
    expect(form.get("sheet")).toBeInstanceOf(File);
    expect(form.get("manifest")).toBeNull();
  });

  it("shows the server's refusal at the field it names", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail: "The sheet is 600 × 600 pixels; the limit is 512 × 512." }),
      })),
    );
    renderDialog();

    fill("Pixel", png());
    fireEvent.click(screen.getByTestId("create-pet-submit"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("the limit is 512");
    expect(alert.id).toContain("sheet");
  });
});

describe("fieldForDetail", () => {
  it("routes a refusal to the part it is about", () => {
    expect(fieldForDetail("manifest: idle is required")).toBe("manifest");
    expect(fieldForDetail("description is longer than 140 characters")).toBe("description");
    expect(fieldForDetail("name must be 1-40 characters")).toBe("name");
    expect(fieldForDetail("not a PNG")).toBe("sheet");
  });
});

describe("CreatePetDialog — from a photo", () => {
  /** jsdom's Image never fires onload; stub the natural size the check reads. */
  function stubImageSize(width: number, height: number) {
    vi.stubGlobal(
      "Image",
      class {
        naturalWidth = width;
        naturalHeight = height;
        onload: (() => void) | null = null;
        onerror: (() => void) | null = null;
        set src(_value: string) {
          queueMicrotask(() => this.onload?.());
        }
      },
    );
  }

  function jpeg(name = "me.jpg", bytes = 64): File {
    return new File([new Uint8Array(bytes)], name, { type: "image/jpeg" });
  }

  function switchToPhoto() {
    fireEvent.click(screen.getByTestId("create-pet-mode-photo"));
  }

  it("starts on the sprite-sheet mode", () => {
    renderDialog();
    expect(screen.getByTestId("create-pet-mode-sheet").getAttribute("aria-checked")).toBe("true");
    expect(screen.getByTestId("create-pet-mode-photo").getAttribute("aria-checked")).toBe("false");
    // The sheet-only controls are the ones on screen.
    expect(screen.getByTestId("create-pet-sheet")).toBeTruthy();
    expect(screen.queryByTestId("create-pet-photo")).toBeNull();
  });

  it("shows the photo controls and hides the sheet controls when switched", () => {
    renderDialog();
    switchToPhoto();
    expect(screen.getByTestId("create-pet-photo")).toBeTruthy();
    expect(screen.queryByTestId("create-pet-sheet")).toBeNull();
    expect(screen.queryByTestId("create-pet-manifest")).toBeNull();
    // The layout explainer and template belong to the sheet mode.
    expect(screen.queryByTestId("create-pet-rows")).toBeNull();
    expect(screen.queryByTestId("create-pet-template")).toBeNull();
  });

  it("states plainly what a photo produces, and where it stays", () => {
    renderDialog();
    switchToPhoto();
    const text = document.body.textContent ?? "";
    expect(text).toContain("stylised portrait whose mouth moves with the voice");
    expect(text).toContain("not a photorealistic talking head");
    expect(text).toContain("Your photo stays on this computer.");
  });

  it("refuses a file that is not an image before uploading anything", () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderDialog();
    switchToPhoto();

    fireEvent.change(screen.getByTestId("create-pet-photo"), {
      target: { files: [new File(["x"], "notes.txt", { type: "text/plain" })] },
    });

    expect(screen.getByRole("alert").textContent).toBe("Choose a PNG or JPEG image.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("refuses a photo over 4 MB", () => {
    renderDialog();
    switchToPhoto();
    fireEvent.change(screen.getByTestId("create-pet-photo"), {
      target: { files: [jpeg("big.jpg", 4 * 1024 * 1024 + 1)] },
    });
    expect(screen.getByRole("alert").textContent).toBe("This photo is larger than 4 MB.");
  });

  it("refuses a photo too small to yield a 48 px frame", async () => {
    stubImageSize(120, 120);
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderDialog();
    switchToPhoto();

    fireEvent.change(screen.getByTestId("create-pet-photo"), { target: { files: [jpeg()] } });

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toBe(
      "This photo is too small. Choose one at least 192 × 192 pixels.",
    );
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("accepts a photo at the minimum size", async () => {
    stubImageSize(192, 192);
    renderDialog();
    switchToPhoto();
    fireEvent.change(screen.getByTestId("create-pet-photo"), { target: { files: [jpeg()] } });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  });

  it("asks for a name before uploading a photo", async () => {
    stubImageSize(600, 800);
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    renderDialog();
    switchToPhoto();
    fireEvent.change(screen.getByTestId("create-pet-photo"), { target: { files: [jpeg()] } });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());

    fireEvent.click(screen.getByTestId("create-pet-submit"));

    expect(screen.getByRole("alert").textContent).toBe("Give your pet a name.");
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("posts the photo to /api/pets/from-photo and hands the new pet back", async () => {
    stubImageSize(600, 800);
    const created = {
      id: "u39f75477602e2586",
      name: "Kasun",
      description: "From my photo.",
      builtin: false,
      frame_size: 48,
      animations: {},
      sheet_url: "/api/pets/u39f75477602e2586/sheet.png",
    };
    const fetchSpy = vi.fn(async () => ({ ok: true, status: 201, json: async () => created }));
    vi.stubGlobal("fetch", fetchSpy);
    const onCreated = renderDialog();

    fireEvent.change(screen.getByTestId("create-pet-name"), { target: { value: "Kasun" } });
    fireEvent.change(screen.getByTestId("create-pet-description"), {
      target: { value: "From my photo." },
    });
    switchToPhoto();
    fireEvent.change(screen.getByTestId("create-pet-photo"), { target: { files: [jpeg()] } });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());

    fireEvent.click(screen.getByTestId("create-pet-submit"));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    const [url, init] = fetchSpy.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe("/api/pets/from-photo");
    expect(init.method).toBe("POST");
    const form = init.body as FormData;
    expect(form.get("name")).toBe("Kasun");
    expect(form.get("description")).toBe("From my photo.");
    expect(form.get("photo")).toBeInstanceOf(File);
    // The photo route takes no frame size or manifest.
    expect(form.get("frame_size")).toBeNull();
    expect(form.get("manifest")).toBeNull();
  });

  it("shows the server's refusal at the photo field", async () => {
    stubImageSize(600, 800);
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({
        ok: false,
        status: 400,
        json: async () => ({ detail: "the photo is too small to build a 48 px frame" }),
      })),
    );
    renderDialog();

    fireEvent.change(screen.getByTestId("create-pet-name"), { target: { value: "Kasun" } });
    switchToPhoto();
    fireEvent.change(screen.getByTestId("create-pet-photo"), { target: { files: [jpeg()] } });
    await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());

    fireEvent.click(screen.getByTestId("create-pet-submit"));

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("too small");
    expect(alert.id).toContain("photo");
  });

  it("routes a refusal to the photo field when the detail names the photo", () => {
    expect(fieldForDetail("photo: not a PNG or JPEG")).toBe("photo");
  });
});
