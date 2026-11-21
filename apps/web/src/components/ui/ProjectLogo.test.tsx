import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ProjectLogo, loadLogo, logoUrl, monogram } from "./ProjectLogo";

const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);

afterEach(() => vi.unstubAllGlobals());

describe("ProjectLogo", () => {
  it("builds initials from keys and display names", () => {
    expect(monogram("hermes")).toBe("HE");
    expect(monogram("LogiSlot")).toBe("LS");
    expect(monogram("fikir-sepeti")).toBe("FS");
    expect(monogram("Fikir Sepeti")).toBe("FS");
    expect(monogram("")).toBe("?");
  });

  it("asks the API for the theme's file, by project id", () => {
    expect(logoUrl("p 1", "dark")).toBe("/v1/projects/p%201/logo?theme=dark");
  });

  it("keeps the monogram when the project has no logo, asking once", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    render(
      <>
        <ProjectLogo projectId="p-none" name="LogiSlot" />
        <ProjectLogo projectId="p-none" name="LogiSlot" variant="mark" />
      </>,
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    for (const logo of screen.getAllByTestId("project-logo")) {
      expect(logo).toHaveAttribute("data-state", "monogram");
      expect(logo).toHaveTextContent("LS");
    }
    // One request for both logos on the page.
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("refuses a 200 that is not an image", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("<svg/>", {
            status: 200,
            headers: { "Content-Type": "text/html" },
          }),
      ),
    );
    expect(await loadLogo("/v1/projects/p-html/logo?theme=dark")).toBeNull();
  });

  it("does not treat a failed request as a logo", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new Error("offline"))),
    );
    expect(await loadLogo("/v1/projects/p-off/logo?theme=dark")).toBeNull();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(PNG, { status: 404 })),
    );
    expect(await loadLogo("/v1/projects/p-404/logo?theme=dark")).toBeNull();
  });
});
