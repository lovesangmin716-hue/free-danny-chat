# Colorless clients

This directory is reserved for independently built client applications. The
Python server and its bundled browser client live in `src/colorless`; future
desktop and mobile clients belong in their respective directories here.

- `desktop/`: desktop application workspace
- `mobile/`: mobile application workspace

Clients should consume the Colorless HTTP and realtime contracts instead of
importing server implementation modules.
