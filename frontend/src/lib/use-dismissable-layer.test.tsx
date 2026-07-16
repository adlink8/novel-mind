import {
  cleanup,
  fireEvent,
  render,
  screen,
  act,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useRef, useState } from "react";

import {
  __resetDismissableLayerStackForTests,
  DISMISSABLE_PRESENCE_MS,
  useDismissableLayer,
} from "./use-dismissable-layer";

beforeEach(() => {
  __resetDismissableLayerStackForTests();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  cleanup();
  __resetDismissableLayerStackForTests();
  vi.useRealTimers();
});

function Panel({
  open,
  onDismiss,
  label,
  ignoreSelectors,
  closeOnOutside,
  closeOnEscape,
  presenceMs = DISMISSABLE_PRESENCE_MS,
}: {
  open: boolean;
  onDismiss: () => void;
  label: string;
  ignoreSelectors?: string[];
  closeOnOutside?: boolean;
  closeOnEscape?: boolean;
  presenceMs?: number;
}) {
  const layerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const { present, closing, interactive } = useDismissableLayer({
    open,
    onDismiss,
    layerRef,
    triggerRef,
    ignoreSelectors,
    closeOnOutside,
    closeOnEscape,
    presenceMs,
  });

  return (
    <div>
      <button ref={triggerRef} type="button" data-testid={`${label}-trigger`}>
        trigger-{label}
      </button>
      {present ? (
        <div
          ref={layerRef}
          data-testid={`${label}-panel`}
          data-closing={closing ? "true" : "false"}
          data-interactive={interactive ? "true" : "false"}
          className={closing ? "motion-closing" : undefined}
        >
          <button type="button" data-testid={`${label}-inner`}>
            inner-{label}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function NestedHarness() {
  const [outer, setOuter] = useState(true);
  const [inner, setInner] = useState(true);
  return (
    <>
      <Panel open={outer} onDismiss={() => setOuter(false)} label="outer" />
      <Panel open={inner} onDismiss={() => setInner(false)} label="inner" />
      <span data-testid="outer-open">{String(outer)}</span>
      <span data-testid="inner-open">{String(inner)}</span>
    </>
  );
}

function ControlledHarness({
  ignoreSelectors,
}: {
  ignoreSelectors?: string[];
}) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" data-extra-ignore>
        extra
      </button>
      <Panel
        open={open}
        onDismiss={() => setOpen(false)}
        label="main"
        ignoreSelectors={ignoreSelectors}
      />
      <span data-testid="open-state">{String(open)}</span>
    </>
  );
}

describe("useDismissableLayer", () => {
  async function settleOpenGuard() {
    await act(async () => {
      // Flush the opening-frame suppressOutside rAF.
      await Promise.resolve();
      vi.runOnlyPendingTimers();
    });
  }

  it("dismisses on outside pointer and not on internal pointer", async () => {
    const onDismiss = vi.fn();
    render(<Panel open onDismiss={onDismiss} label="a" />);
    await settleOpenGuard();

    fireEvent.pointerDown(screen.getByTestId("a-inner"));
    expect(onDismiss).not.toHaveBeenCalled();

    fireEvent.pointerDown(document.body);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("does not treat trigger pointer as outside", async () => {
    const onDismiss = vi.fn();
    render(<Panel open onDismiss={onDismiss} label="a" />);
    await settleOpenGuard();
    fireEvent.pointerDown(screen.getByTestId("a-trigger"));
    expect(onDismiss).not.toHaveBeenCalled();
  });

  it("honors ignoreSelectors for outside protection", async () => {
    render(<ControlledHarness ignoreSelectors={["[data-extra-ignore]"]} />);
    await settleOpenGuard();
    fireEvent.pointerDown(screen.getByText("extra"));
    expect(screen.getByTestId("open-state").textContent).toBe("true");
    fireEvent.pointerDown(document.body);
    expect(screen.getByTestId("open-state").textContent).toBe("false");
  });

  it("dismisses on Escape", () => {
    const onDismiss = vi.fn();
    render(<Panel open onDismiss={onDismiss} label="a" />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("only dismisses the topmost nested layer", async () => {
    render(<NestedHarness />);
    await settleOpenGuard();
    fireEvent.pointerDown(document.body);
    expect(screen.getByTestId("inner-open").textContent).toBe("false");
    expect(screen.getByTestId("outer-open").textContent).toBe("true");

    fireEvent.pointerDown(document.body);
    expect(screen.getByTestId("outer-open").textContent).toBe("false");
  });

  it("Escape only closes the topmost nested layer", () => {
    render(<NestedHarness />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByTestId("inner-open").textContent).toBe("false");
    expect(screen.getByTestId("outer-open").textContent).toBe("true");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByTestId("outer-open").textContent).toBe("false");
  });

  it("protects against the opening pointer race", async () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <Panel open={false} onDismiss={onDismiss} label="a" />
    );
    rerender(<Panel open onDismiss={onDismiss} label="a" />);
    // Immediately after open — suppressOutside still true
    fireEvent.pointerDown(document.body);
    expect(onDismiss).not.toHaveBeenCalled();

    await settleOpenGuard();
    fireEvent.pointerDown(document.body);
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it("holds present while closing then unmounts after presenceMs", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <Panel open onDismiss={onDismiss} label="a" presenceMs={300} />
    );
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-closing",
      "false"
    );

    rerender(
      <Panel open={false} onDismiss={onDismiss} label="a" presenceMs={300} />
    );
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-closing",
      "true"
    );
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-interactive",
      "false"
    );

    act(() => {
      vi.advanceTimersByTime(299);
    });
    expect(screen.getByTestId("a-panel")).toBeInTheDocument();

    act(() => {
      vi.advanceTimersByTime(2);
    });
    expect(screen.queryByTestId("a-panel")).not.toBeInTheDocument();
  });

  it("supports rapid reopen during exit presence", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <Panel open onDismiss={onDismiss} label="a" presenceMs={300} />
    );
    rerender(
      <Panel open={false} onDismiss={onDismiss} label="a" presenceMs={300} />
    );
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-closing",
      "true"
    );

    rerender(<Panel open onDismiss={onDismiss} label="a" presenceMs={300} />);
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-closing",
      "false"
    );
    expect(screen.getByTestId("a-panel")).toHaveAttribute(
      "data-interactive",
      "true"
    );
  });

  it("restores focus to the trigger after close presence ends", () => {
    function FocusHarness() {
      const [open, setOpen] = useState(true);
      const layerRef = useRef<HTMLDivElement>(null);
      const triggerRef = useRef<HTMLButtonElement>(null);
      const { present } = useDismissableLayer({
        open,
        onDismiss: () => setOpen(false),
        layerRef,
        triggerRef,
        presenceMs: 50,
      });
      return (
        <div>
          <button ref={triggerRef} type="button" data-testid="focus-trigger">
            open
          </button>
          {present ? (
            <div ref={layerRef} data-testid="focus-panel">
              <button
                type="button"
                data-testid="focus-close"
                onClick={() => setOpen(false)}
              >
                close
              </button>
            </div>
          ) : null}
        </div>
      );
    }

    render(<FocusHarness />);
    const trigger = screen.getByTestId("focus-trigger");
    trigger.focus();
    expect(document.activeElement).toBe(trigger);

    screen.getByTestId("focus-close").focus();
    fireEvent.click(screen.getByTestId("focus-close"));

    act(() => {
      vi.advanceTimersByTime(60);
    });
    expect(document.activeElement).toBe(trigger);
  });

  it("cleans up listeners on unmount without throwing", () => {
    const onDismiss = vi.fn();
    const { unmount } = render(
      <Panel open onDismiss={onDismiss} label="a" />
    );
    unmount();
    expect(() => {
      fireEvent.pointerDown(document.body);
      fireEvent.keyDown(document, { key: "Escape" });
    }).not.toThrow();
    expect(onDismiss).not.toHaveBeenCalled();
  });
});
