import { createEventRouter, createRealtimeClient } from "./events.js";
import { createHttpClient, HttpError } from "./http.js";
import { createIcon, decorateIconButton, hydrateIcons } from "./icons.js";
import { createActionPipeline } from "./pipeline.js";
import { createStore } from "./store.js";

export const ColorlessPlatform = Object.freeze({
  createActionPipeline,
  createEventRouter,
  createHttpClient,
  createIcon,
  createRealtimeClient,
  createStore,
  decorateIconButton,
  HttpError,
  hydrateIcons,
});
