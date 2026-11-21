import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ProjectLogo, logoUrl, monogram } from "./ProjectLogo";

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

  it("shows the monogram until the image has loaded, then the image", () => {
    const { container } = render(<ProjectLogo projectId="p-load" name="hermes" />);
    const root = screen.getByTestId("project-logo");
    expect(root).toHaveAttribute("data-state", "monogram");
    expect(root).toHaveTextContent("HE");
    const img = container.querySelector("img")!;
    expect(img).toHaveAttribute("alt", "");
    expect(img.getAttribute("src")).toMatch(/^\/v1\/projects\/p-load\/logo\?theme=(light|dark)$/);
    fireEvent.load(img);
    expect(root).toHaveAttribute("data-state", "image");
    expect(root).not.toHaveTextContent("HE");
  });

  it("keeps the monogram and drops the image when there is no logo", () => {
    const { container, unmount } = render(<ProjectLogo projectId="p-missing" name="logislot" />);
    fireEvent.error(container.querySelector("img")!);
    expect(screen.getByTestId("project-logo")).toHaveAttribute("data-state", "monogram");
    expect(container.querySelector("img")).toBeNull();
    unmount();
    // Remembered for the session: a second mount does not ask again.
    const again = render(<ProjectLogo projectId="p-missing" name="logislot" />);
    expect(again.container.querySelector("img")).toBeNull();
  });
});
