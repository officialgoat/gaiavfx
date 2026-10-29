# GAIAVFX design system

<!-- impeccable:design-schema 1 -->

The application uses a compact editing-suite layout with local Inter, docked work areas and media-led composition. The tokens below and the frontend styles are the design authority.

## Working surface

Operate mode: a desktop filmmaking workstation with a Premiere-style menu bar, workspace tabs, docked panel groups and central tracks. Direct opens Project at 218 px, Source/Program above the timeline at 245 px, and Chat/Properties/Jobs at 420 px. Five-pixel seams resize with pointer or keyboard. The full-width layout places the timeline across the bottom with a taller monitor row. Layout and panel-tab preferences persist per workspace; the focused panel has an accent ring. Narrow windows retain all panels with bounded workspace scrolling.

Chat is a primary dock with a fixed 29 px model status line, scrolling transcript and docked composer. Plan mode is on by default. The Agent workspace reuses this panel at 540–800 px beside the timeline; Direct keeps its 420 px conversation dock. Export reuses the dock with an ordered selected-take list, explicit changed-take confirmation, local assembly progress and cancellation, and a result action that opens the delivered movie in Program.

Prepare extends the same dock with a plate → views → pin → sheet strip above the central tracks. Source opens the selected attempt for an explicit approval or rejection; rejected attempts remain visible and dimmed. Binding staleness and suitability use words beside their state colour. Review replaces the monitor row with synchronized, letter-labelled candidates and uses the Properties dock for ranking, ties, the usable line, tags and notes. Candidate names and provenance stay absent until an explicit Reveal after ranking. Both workspaces retain Project, timeline, resizable seams and per-workspace layout preferences.

Conversation uses 14 px prose at a maximum 70ch measure, quiet system dividers and collapsed tool details. Plans show their own job count, numbered steps, explicit human review stops and revision-checked approval. Earlier revisions collapse together. In the full-width layout, approved plans use a compact progress summary with the conversation and full plan retained under expandable details; Plan details returns to the default layout. Attachment chips show source thumbnails. Attachments, mentions, receipt links and timeline links use the shared focus treatment. Message drafts, attachments and mentions survive dock-tab changes within the open session. No candidate provenance appears in tool details during blind review.

## Tokens

| Role           | Dark      | Light     |
| -------------- | --------- | --------- |
| Canvas         | `#1b1b1e` | `#e8e7e3` |
| Panel          | `#232326` | `#f4f3ef` |
| Raised         | `#2c2c30` | `#fffefa` |
| Hover          | `#35353a` | `#e6e3db` |
| Hairline       | `#3a3a40` | `#cfcdc5` |
| Text           | `#eeeeef` | `#292925` |
| Muted          | `#b1b1ba` | `#605f58` |
| Secondary      | `#a3a3af` | `#6c685f` |
| Sidebar        | `#252528` | `#eeede7` |
| Media matte    | `#121214` | `#19191b` |
| Accent         | `#aab8f0` | `#825c2c` |
| Action         | `#5265a2` | `#8b632f` |
| Action hover   | `#5a6eac` | `#745027` |
| Selection      | `#343b50` | `#e9dfce` |
| Playhead / pin | `#c3a6e8` | `#94672e` |
| Phase          | `#363c42` | `#e0e3e2` |
| Picture        | `#3c3746` | `#e5dfeb` |
| Audio          | `#9ab9af` | `#496b5c` |
| Audio fill     | `#2c3c37` | `#dce6df` |
| Danger         | `#e8a6a2` | `#a23e36` |
| Reviewed       | `#aac9b1` | `#3f6848` |
| Needs review   | `#d9be8e` | `#805c25` |

Iris / ochre is the default. Steel / bronze remains a hidden CSS token set, without a product preference control. Semantic colour always has words or an icon.

## Type, geometry and interaction

- Locally bundled Inter 400/500/600; 12 px working labels, 11 px metadata, 14 px prose, 16 px inspector headings, tabular 12–13 px timecodes.
- Spacing: 4 / 8 / 10 / 12 / 16 / 24 px. Media 3 px, compact controls 4 px, inputs 5 px, popups 6 px. One-pixel neutral dividers.
- Focus: 2 px accent outline, 3 px offset. Every panel is keyboard focusable.
- Menu elevation: `0 10px 28px #0006` dark / `0 10px 28px #3332` light. Raised opaque fallback; 96% raised and 18 px blur where supported.
- Hover 150 ms ease-out; reduced motion disables transitions. No page entrances.
- Menus, item context menus and shortcuts resolve a single command registry. Disabled commands explain their reason. Context menus support Shift+F10, arrows, submenus, Escape and focus return.
- Timeline time is delivered frames at 24 fps for movies; a film keeps its own rational frame rate and a timecode ruler. Ruler lines measure time; picture spans describe reference scope, not screen occupancy, while a film's Cast lanes show where a face is seen. Keyframes anchor to their cut plus the API plan offset. Coverage, technique and compatibility come from Python, never a renderer compiler.
- Loading, empty, error, conflict and retry states remain legible in both themes. Original media, selected takes and candidate previews remain distinct. Blind review projections must not expose job, seed or technique associations.
