import { useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { HeaderStatusSlotContext } from "./headerStatusSlot";

/** Renders its children into the application header's status slot, if any. */
export function HeaderStatus({ children }: { children: ReactNode }) {
  const slot = useContext(HeaderStatusSlotContext);
  return slot === null ? (
    <div className="header-status-inline">{children}</div>
  ) : (
    createPortal(children, slot)
  );
}
