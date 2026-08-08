import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LocalSignIn } from "@/components/auth/LocalSignIn";

/**
 * The sign-in form's job is to be unhelpful about failures: one message,
 * whatever went wrong, matching what the server discloses.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function fill(email: string, password: string) {
  fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
  fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  fireEvent.click(screen.getByRole("button", { name: /sign in/i }));
}

describe("local sign-in", () => {
  it("posts credentials to the API as JSON with cookies", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
    // jsdom cannot navigate; replacing the whole location object is the
    // supported way to observe the attempt without one.
    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign },
    });

    render(<LocalSignIn />);
    fill("someone@example.test", "a-test-password");

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/v1/auth/login");
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
    expect(JSON.parse(init.body)).toEqual({
      email: "someone@example.test",
      password: "a-test-password",
    });
  });

  it("says the same thing for a wrong password and an unknown account", async () => {
    const messages: string[] = [];
    for (const status of [401, 401]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status }));
      const view = render(<LocalSignIn />);
      fill("someone@example.test", "wrong");
      const alert = await screen.findByRole("alert");
      messages.push(alert.textContent ?? "");
      view.unmount();
    }
    expect(messages[0]).toBe(messages[1]);
    expect(messages[0]).not.toMatch(/no such|unknown|not found|does not exist/i);
  });

  it("distinguishes throttling and outage, which are not credential hints", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 429 }));
    const view = render(<LocalSignIn />);
    fill("someone@example.test", "x");
    expect((await screen.findByRole("alert")).textContent).toMatch(/too many attempts/i);
    view.unmount();

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 503 }));
    render(<LocalSignIn />);
    fill("someone@example.test", "x");
    expect((await screen.findByRole("alert")).textContent).toMatch(/unavailable/i);
  });

  it("keeps the typed password on failure and drops it on success", async () => {
    // On failure the field must keep its value: clearing it would force a
    // retype for what is usually a typo, and reveals nothing.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));
    const view = render(<LocalSignIn />);
    fill("someone@example.test", "a-test-password");
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Password")).toHaveValue("a-test-password");
    // ...and the error message itself never quotes it back.
    expect(screen.getByRole("alert").textContent).not.toContain("a-test-password");
    view.unmount();

    // On success it is dropped from component state before anything else.
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, status: 200 }));
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...window.location, assign: vi.fn() },
    });
    render(<LocalSignIn />);
    fill("someone@example.test", "a-test-password");
    await waitFor(() => expect(screen.getByLabelText("Password")).toHaveValue(""));
  });

  it("marks the password field so browsers and assistive tech treat it as one", () => {
    vi.stubGlobal("fetch", vi.fn());
    render(<LocalSignIn />);
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByLabelText("Email")).toHaveAttribute("autocomplete", "username");
  });
});
