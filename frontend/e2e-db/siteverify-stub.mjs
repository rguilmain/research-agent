// Minimal stand-in for Cloudflare's siteverify endpoint so the browser suite never leaves the
// machine. It is only ever pointed at by TURNSTILE_VERIFY_URL in development/test runs;
// production readiness refuses any endpoint other than Cloudflare's.
import { createServer } from 'node:http'

const PORT = Number(process.env.SITEVERIFY_PORT ?? 8124)
const HOST = '127.0.0.1'
const SUCCESS = JSON.stringify({
  success: true,
  challenge_ts: '2026-01-01T00:00:00.000Z',
  hostname: 'example.com',
  'error-codes': [],
})

const json = (response, status, body) => {
  response.writeHead(status, { 'content-type': 'application/json' })
  response.end(body)
}

const server = createServer((request, response) => {
  // The token is never inspected: the stub always reports success, like Cloudflare's
  // documented always-pass test secret does.
  request.resume()
  if (request.method === 'GET' && request.url === '/healthz') {
    json(response, 200, '{"status":"ok"}')
    return
  }
  if (request.method === 'POST' && request.url?.startsWith('/turnstile/v0/siteverify')) {
    json(response, 200, SUCCESS)
    return
  }
  json(response, 404, '{"success":false}')
})

server.listen(PORT, HOST, () => {
  console.log(`Turnstile siteverify stub listening on http://${HOST}:${PORT}`)
})
