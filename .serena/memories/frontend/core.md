# Frontend core
- React 19 + TypeScript + Vite workbench under `frontend`.
- Uses React Router, TanStack Query, Ant Design, Lucide icons.
- Unit/UI tests: Vitest + Testing Library; end-to-end: Playwright.
- Development scripts: `npm run dev` launches coordinated development flow; `npm run dev:web` launches only Vite.
- Quality gates: `npm test -- --run`, `npm run lint`, `npm run typecheck`, `npm run build`; add `npm run test:e2e` for browser flows.
- Do not send trusted tenant/operator identity from browser state.