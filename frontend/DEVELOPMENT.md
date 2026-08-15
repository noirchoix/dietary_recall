# Frontend development service

The SvelteKit frontend is an independent process. It does not start Python and
the Python backend does not serve frontend files.

## Start the frontend

From `frontend`:

```bash
npm install
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies browser requests under `/api` to
`http://127.0.0.1:8765`, preserving the frontend's same-origin cookie and CSRF
flow during local development.

To use another backend origin, copy `.env.example` to `.env.local` and change
`DIETARY_RECALL_API_PROXY_TARGET`. This value is an origin such as
`http://127.0.0.1:9000`; paths, queries and fragments are rejected.

Backend credentials and payment-provider secrets must never be placed in a
frontend environment file. Configure them only in the backend process.
