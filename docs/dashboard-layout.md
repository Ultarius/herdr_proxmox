# Dashboard layout and appearance

The overview groups live agent counts, workspaces, and connection state above
compact agent and workspace panels. Agent filters use the actual runtime state;
working/running/busy are grouped under Working, and idle/done/waiting under Idle.
Unknown and blocked states remain visible under All. Workspace menus retain
Focus in Herdr and Rename actions. Create workspace opens the existing project
setup section, including repository cloning.

Desktop navigation uses a sidebar. Below 1000 pixels, the application uses a
drawer for the complete navigation and a bottom bar for the main destinations.
Panels switch from columns to stacked rows below 650 pixels. Status labels
include text, so meaning does not depend on color alone.

The Appearance menu offers Light mode, Dark mode, and Use device theme. The
choice is stored in this browser's local storage; unavailable storage does not
prevent using the application. Organization and group surfaces follow the
active theme as well.

System health retains the existing resource sampler, error backoff, pressure
warnings, and process details. Recent activity reports agent changes observed
while this overview is open. It is not a persistent audit log and starts empty;
integration audit history remains in the integration board.

Layout regression tests cover 320, 390, and 1440 pixel viewports in both themes,
including long names, status filtering, and workspace actions. The resource
monitor is also tested at 320 pixels.
