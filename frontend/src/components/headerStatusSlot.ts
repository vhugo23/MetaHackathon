import { createContext } from "react";

/**
 * The element inside the application header that hosts the live-workspace
 * connection status. `null` when the workspace is rendered outside `App`
 * (e.g. in isolation), in which case it renders the status inline.
 */
export const HeaderStatusSlotContext = createContext<HTMLElement | null>(null);
