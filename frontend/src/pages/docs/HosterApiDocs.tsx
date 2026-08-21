import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowLeft, KeyRound, Link2, ListChecks, Plug, Radio, ShieldCheck, Signature, Table2 } from 'lucide-react'
import { CodeBlock } from '@/components/docs/CodeBlock'
import { PageHeader } from '@/Singra/UI/PageHeader'

/**
 * Endpunkt- und Webhook-Referenz fuer Shop-Anbindungen.
 *
 * Die Werte hier sind kein Prosatext, sondern ein Vertrag: Pfade, Feldnamen,
 * Eventnamen und das Signaturbeispiel muessen exakt dem entsprechen, was das
 * Backend tut. `backend/tests/test_hoster_api_docs_contract.py` prueft dieselben
 * Werte gegen `docs/hoster-api.md`, damit beide Fassungen nicht auseinanderlaufen.
 */

export const SIGNATURE_EXAMPLE_SECRET = 'whsec_msm_beispiel_nicht_verwenden'
export const SIGNATURE_EXAMPLE_TIMESTAMP = '1786120930'
export const SIGNATURE_EXAMPLE_BODY =
  '{"event":"service.ready","external_service_id":"SVC-4711","desired_state":"active",'
  + '"status":"ready","status_code":null,"server_id":42,'
  + '"correlation_id":"6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14","terminate_after":null,'
  + '"updated_at":"2026-08-08T09:22:10+00:00"}'
export const SIGNATURE_EXAMPLE_DIGEST =
  'sha256=c22272c50fb68bae6f99965c33f831b7e3197d9766c00b0fc216b6c5289a51b0'

const HEALTH_COMMAND = `curl -sS https://panel.example/api/hoster/v1/health \\
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY"`

const ORDER_COMMAND = `curl -X PUT https://panel.example/api/hoster/v1/services/SVC-4711 \\
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{
        "desired_state": "active",
        "external_subject": "CUST-1234",
        "product_key": "mc-8gb",
        "email": "kunde@example.com"
      }'`

const SERVICE_RESPONSE = `{
  "external_service_id": "SVC-4711",
  "desired_state": "active",
  "status": "ready",
  "status_code": null,
  "server_id": 42,
  "task_id": "0a3f1b7c-2d4e-4f60-9a11-8c5e2f7b6d33",
  "correlation_id": "6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14",
  "terminate_after": null,
  "updated_at": "2026-08-08T09:22:10+00:00"
}`

const WEBHOOK_HEADERS = `Content-Type: application/json
X-MSM-Timestamp: 1786120930
X-MSM-Signature: sha256=<hex>
X-MSM-Event: service.ready
User-Agent: MSM-Hoster-Webhook/1.0`

const WEBHOOK_BODY = `{
  "event": "service.ready",
  "external_service_id": "SVC-4711",
  "desired_state": "active",
  "status": "ready",
  "status_code": null,
  "server_id": 42,
  "correlation_id": "6f6d9d1e-6b1e-4a51-9f0c-2b7a5d3e8c14",
  "terminate_after": null,
  "updated_at": "2026-08-08T09:22:10+00:00"
}`

const VERIFY_PHP = `<?php
// Rohbody, NICHT $_POST und nicht json_decode/json_encode.
$body      = file_get_contents('php://input');
$timestamp = $_SERVER['HTTP_X_MSM_TIMESTAMP'] ?? '';
$signature = $_SERVER['HTTP_X_MSM_SIGNATURE'] ?? '';
$secret    = getenv('MSM_WEBHOOK_SECRET');

if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300) {
    http_response_code(400);
    exit;
}

$expected = 'sha256=' . hash_hmac('sha256', $timestamp . '.' . $body, $secret);
if (!hash_equals($expected, $signature)) {
    http_response_code(401);
    exit;
}

$payload = json_decode($body, true);
http_response_code(200);`

const VERIFY_PYTHON = `import hashlib, hmac, os, time

SECRET = os.environ["MSM_WEBHOOK_SECRET"].encode()
TOLERANCE_SECONDS = 300


def verify(raw_body: bytes, timestamp: str, signature: str) -> bool:
    if not timestamp.isdigit() or abs(time.time() - int(timestamp)) > TOLERANCE_SECONDS:
        return False
    expected = "sha256=" + hmac.new(
        SECRET, f"{timestamp}.".encode() + raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)`

const HANDOFF_COMMAND = `curl -X POST https://panel.example/api/hoster/v1/handoffs \\
  -H "X-MSM-Hoster-Key: $MSM_HOSTER_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{"external_service_id":"SVC-4711","target_path":"/servers/42"}'`

const ENDPOINTS = [
  ['GET', '/api/hoster/v1/health', 'health'],
  ['PUT', '/api/hoster/v1/services/{external_service_id}', 'put'],
  ['GET', '/api/hoster/v1/services/{external_service_id}', 'get'],
  ['POST', '/api/hoster/v1/handoffs', 'handoff'],
  ['GET', '/api/hoster/handoff/{token}', 'redeem'],
] as const

const REQUEST_FIELDS = ['desired_state', 'external_subject', 'product_key', 'email'] as const
const RESPONSE_FIELDS = [
  'external_service_id',
  'desired_state',
  'status',
  'status_code',
  'server_id',
  'task_id',
  'correlation_id',
  'terminate_after',
  'updated_at',
] as const
const DESIRED_STATES = ['active', 'suspended', 'terminated'] as const
const SERVICE_STATUSES = [
  'pending',
  'provisioning',
  'ready',
  'suspended',
  'terminating',
  'terminated',
  'failed',
] as const
const HTTP_CODES = ['200', '401', '404', '422', '503'] as const
const STATUS_CODES = [
  'port_conflict',
  'node_not_found',
  'install_directory_exists',
  'install_update_already_running',
  'hoster_configuration_error',
  'hoster_role_escalation',
  'hoster_internal_error',
  'product_changed_manual_resize_required',
] as const
const DELIVERY_FACTS = ['timeout', 'attempts', 'backoff', 'clientError', 'serverError', 'redirects', 'retention'] as const
const RECEIVER_RULES = ['constantTime', 'replay', 'idempotent', 'ordering', 'fast'] as const
const HANDOFF_FACTS = ['ttl', 'once', 'bound', 'targets', 'hashOnly', 'concurrent', 'uniformError'] as const

const ADMIN_ENDPOINTS = [
  ['GET', '/integrations', 'panel.hoster.read', 'list'],
  ['POST', '/integrations', 'panel.hoster.write', 'create'],
  ['PATCH', '/integrations/{integration_id}', 'panel.hoster.write', 'update'],
  ['POST', '/integrations/{integration_id}/api-key', 'panel.hoster.write', 'rotateKey'],
  ['POST', '/integrations/{integration_id}/webhook-secret', 'panel.hoster.write', 'rotateSecret'],
  ['DELETE', '/integrations/{integration_id}', 'panel.hoster.write', 'delete'],
  ['GET', '/integrations/{integration_id}/products', 'panel.hoster.read', 'listProducts'],
  ['PUT', '/integrations/{integration_id}/products', 'panel.hoster.write', 'putProduct'],
  ['DELETE', '/integrations/{integration_id}/products/{product_id}', 'panel.hoster.write', 'deleteProduct'],
  ['GET', '/integrations/{integration_id}/services', 'panel.hoster.read', 'listServices'],
  ['GET', '/integrations/{integration_id}/deliveries', 'panel.hoster.read', 'listDeliveries'],
  ['POST', '/integrations/{integration_id}/deliveries/{delivery_id}/retry', 'panel.hoster.write', 'retryDelivery'],
  ['POST', '/integrations/{integration_id}/simulate', 'panel.hoster.write', 'simulate'],
  ['DELETE', '/integrations/{integration_id}/sandbox-data', 'panel.hoster.write', 'cleanSandbox'],
] as const

const SECTIONS = [
  ['principle', 'principle'],
  ['auth', 'auth'],
  ['endpoints', 'endpoints'],
  ['states', 'states'],
  ['errors', 'errors'],
  ['webhooks', 'webhooks'],
  ['signature', 'signature'],
  ['handoff', 'handoff'],
  ['admin', 'admin'],
] as const

function SectionHeading({ id, icon, title }: { id: string; icon: React.ReactNode; title: string }) {
  return (
    <div className="mb-4 flex items-center gap-2">
      <span className="text-primary">{icon}</span>
      <h2 id={id} className="font-headline text-headline-md text-on-surface">{title}</h2>
    </div>
  )
}

function DefinitionTable({ rows }: { rows: { term: string; body: string; badge?: string }[] }) {
  return (
    <dl className="grid gap-px overflow-hidden rounded-xl border border-outline-variant bg-outline-variant md:grid-cols-2">
      {rows.map(row => (
        <div key={row.term} className="bg-surface-container p-4">
          <dt className="flex flex-wrap items-center gap-2 text-sm font-semibold text-on-surface">
            <code className="rounded bg-surface-container-highest px-1.5 py-0.5 font-mono text-xs">{row.term}</code>
            {row.badge && (
              <span className="rounded-full bg-surface-container-highest px-2 py-0.5 font-mono text-[11px] text-on-surface-variant">
                {row.badge}
              </span>
            )}
          </dt>
          <dd className="mt-1 text-sm leading-6 text-on-surface-variant">{row.body}</dd>
        </div>
      ))}
    </dl>
  )
}

export function HosterApiDocs() {
  const { t } = useTranslation()

  return (
    <main className="msm-page mx-auto max-w-6xl">
      <PageHeader
        eyebrow={t('docsHosterApi.eyebrow')}
        title={t('docsHosterApi.title')}
        description={t('docsHosterApi.subtitle')}
        status={<Plug className="h-6 w-6 text-primary" aria-hidden="true" />}
      />

      <Link to="/docs" className="msm-btn-secondary mb-6 inline-flex items-center gap-2 px-3 py-2 text-sm">
        <ArrowLeft className="h-4 w-4" />
        {t('docsHosterApi.backToDocs')}
      </Link>

      <nav
        className="sticky top-16 z-10 -mx-1 mb-6 flex gap-2 overflow-x-auto bg-surface/95 px-1 py-2 backdrop-blur"
        aria-label={t('docsHosterApi.navigationLabel')}
      >
        {SECTIONS.map(([id, key]) => (
          <a key={id} href={`#${id}`} className="msm-btn-secondary shrink-0 px-3 py-2 text-xs">
            {t(`docsHosterApi.${key}.title`)}
          </a>
        ))}
      </nav>

      {/* Ohne angelegte Integration passiert nichts von alledem — das steht
          bewusst ganz oben, weil Self-Hosting der Normalfall ist. */}
      <aside className="mb-8 flex gap-3 rounded-xl border border-outline-variant bg-surface-container p-4" aria-labelledby="optional-note">
        <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0 text-primary" />
        <p id="optional-note" className="text-sm leading-6 text-on-surface-variant">
          {t('docsHosterApi.optionalNote')}
        </p>
      </aside>

      <section aria-labelledby="principle" className="mb-10">
        <SectionHeading id="principle" icon={<ListChecks className="h-5 w-5" />} title={t('docsHosterApi.principle.title')} />
        <p className="mb-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.principle.desiredState')}</p>
        <p className="mb-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.principle.noInternals')}</p>
        <p className="max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.principle.sharedLogic')}</p>
      </section>

      <section aria-labelledby="auth" className="mb-10">
        <SectionHeading id="auth" icon={<KeyRound className="h-5 w-5" />} title={t('docsHosterApi.auth.title')} />
        <p className="mb-1 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.auth.header')}</p>
        <CodeBlock code="X-MSM-Hoster-Key: <api-key>" label={t('docsHosterApi.auth.headerLabel')} testId="hoster-auth-header" />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.auth.noCookie')}</p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.auth.hashOnly')}</p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.auth.boundary')}</p>
        <CodeBlock code={HEALTH_COMMAND} label={t('docsHosterApi.auth.healthLabel')} testId="hoster-health-command" />
      </section>

      <section aria-labelledby="endpoints" className="mb-10">
        <SectionHeading id="endpoints" icon={<Table2 className="h-5 w-5" />} title={t('docsHosterApi.endpoints.title')} />
        <DefinitionTable
          rows={ENDPOINTS.map(([method, path, key]) => ({
            term: path,
            badge: method,
            body: t(`docsHosterApi.endpoints.${key}`),
          }))}
        />

        <h3 className="mt-8 mb-3 font-headline text-title-lg text-on-surface">{t('docsHosterApi.endpoints.requestTitle')}</h3>
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.endpoints.idempotencyKey')}</p>
        <DefinitionTable
          rows={REQUEST_FIELDS.map(field => ({
            term: field,
            badge: t(`docsHosterApi.request.${field}.type`),
            body: t(`docsHosterApi.request.${field}.body`),
          }))}
        />
        <CodeBlock code={ORDER_COMMAND} label={t('docsHosterApi.endpoints.orderLabel')} testId="hoster-order-command" />

        <h3 className="mt-8 mb-3 font-headline text-title-lg text-on-surface">{t('docsHosterApi.endpoints.responseTitle')}</h3>
        <DefinitionTable
          rows={RESPONSE_FIELDS.map(field => ({
            term: field,
            badge: t(`docsHosterApi.response.${field}.type`),
            body: t(`docsHosterApi.response.${field}.body`),
          }))}
        />
        <CodeBlock code={SERVICE_RESPONSE} label={t('docsHosterApi.endpoints.responseLabel')} testId="hoster-service-response" />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.endpoints.repeatable')}</p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.endpoints.async')}</p>
      </section>

      <section aria-labelledby="states" className="mb-10">
        <SectionHeading id="states" icon={<ListChecks className="h-5 w-5" />} title={t('docsHosterApi.states.title')} />
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.states.intro')}</p>

        <h3 className="mb-3 font-headline text-title-lg text-on-surface">{t('docsHosterApi.states.desiredTitle')}</h3>
        <DefinitionTable
          rows={DESIRED_STATES.map(state => ({ term: state, body: t(`docsHosterApi.desired.${state}`) }))}
        />

        <h3 className="mt-8 mb-3 font-headline text-title-lg text-on-surface">{t('docsHosterApi.states.actualTitle')}</h3>
        <DefinitionTable
          rows={SERVICE_STATUSES.map(status => ({ term: status, body: t(`docsHosterApi.status.${status}`) }))}
        />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.states.purge')}</p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.states.productChange')}</p>
        {/* Die Produktrolle haengt am Zielzustand, nicht am tatsaechlichen — deshalb hier
            und nicht bei den Statuswerten. */}
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.states.role')}</p>
        {/* Die eine Zusage, die ein Shop-Entwickler sonst falsch raet: entzogen
            wird das Vergebene, nicht das heute am Produkt Stehende. */}
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.states.roleRecord')}</p>
      </section>

      <section aria-labelledby="errors" className="mb-10">
        <SectionHeading id="errors" icon={<AlertTriangle className="h-5 w-5" />} title={t('docsHosterApi.errors.title')} />
        <DefinitionTable rows={HTTP_CODES.map(code => ({ term: code, body: t(`docsHosterApi.http.${code}`) }))} />
        <p className="mt-4 mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.errors.persisted')}</p>
        <DefinitionTable rows={STATUS_CODES.map(code => ({ term: code, body: t(`docsHosterApi.statusCode.${code}`) }))} />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.errors.roleEscalation')}</p>
      </section>

      <section aria-labelledby="webhooks" className="mb-10">
        <SectionHeading id="webhooks" icon={<Radio className="h-5 w-5" />} title={t('docsHosterApi.webhooks.title')} />
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.webhooks.intro')}</p>
        <CodeBlock
          code={SERVICE_STATUSES.map(status => `service.${status}`).join('\n')}
          label={t('docsHosterApi.webhooks.eventsLabel')}
          testId="hoster-webhook-events"
        />
        <CodeBlock code={WEBHOOK_HEADERS} label={t('docsHosterApi.webhooks.headersLabel')} testId="hoster-webhook-headers" />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.webhooks.bodyIntro')}</p>
        <CodeBlock code={WEBHOOK_BODY} label={t('docsHosterApi.webhooks.bodyLabel')} testId="hoster-webhook-body" />
        <p className="mt-4 mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.webhooks.noInternals')}</p>
        <DefinitionTable
          rows={DELIVERY_FACTS.map(fact => ({
            term: t(`docsHosterApi.delivery.${fact}.term`),
            body: t(`docsHosterApi.delivery.${fact}.body`),
          }))}
        />
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.webhooks.durable')}</p>
        {/* Stilles Verwerfen ist fuer den Empfaenger unsichtbar — deshalb als Warnung, nicht als Fussnote. */}
        <aside className="mt-4 flex gap-3 rounded-xl border border-status-warning/30 bg-status-warning/10 p-4 text-status-warning">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <p className="text-sm leading-6">{t('docsHosterApi.webhooks.payloadLimit')}</p>
        </aside>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.webhooks.clientError')}</p>
      </section>

      <section aria-labelledby="signature" className="mb-10">
        <SectionHeading id="signature" icon={<Signature className="h-5 w-5" />} title={t('docsHosterApi.signature.title')} />
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.signature.formula')}</p>
        <aside className="mb-4 flex gap-3 rounded-xl border border-status-warning/30 bg-status-warning/10 p-4 text-status-warning">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <p className="text-sm leading-6">{t('docsHosterApi.signature.rawBody')}</p>
        </aside>
        <p className="mb-2 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.signature.exampleIntro')}</p>
        <CodeBlock
          code={[
            `Secret:     ${SIGNATURE_EXAMPLE_SECRET}`,
            `Timestamp:  ${SIGNATURE_EXAMPLE_TIMESTAMP}`,
            `Body:       ${SIGNATURE_EXAMPLE_BODY}`,
            '',
            `Erwartet:   ${SIGNATURE_EXAMPLE_DIGEST}`,
          ].join('\n')}
          label={t('docsHosterApi.signature.exampleLabel')}
          testId="hoster-signature-example"
        />
        <CodeBlock code={VERIFY_PHP} label="PHP" testId="hoster-verify-php" />
        <CodeBlock code={VERIFY_PYTHON} label="Python" testId="hoster-verify-python" />
        <h3 className="mt-8 mb-3 font-headline text-title-lg text-on-surface">{t('docsHosterApi.signature.rulesTitle')}</h3>
        <DefinitionTable
          rows={RECEIVER_RULES.map(rule => ({
            term: t(`docsHosterApi.receiver.${rule}.term`),
            body: t(`docsHosterApi.receiver.${rule}.body`),
          }))}
        />
      </section>

      <section aria-labelledby="handoff" className="mb-10">
        <SectionHeading id="handoff" icon={<Link2 className="h-5 w-5" />} title={t('docsHosterApi.handoff.title')} />
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.handoff.intro')}</p>
        <DefinitionTable
          rows={[
            { term: 'external_service_id', badge: t('docsHosterApi.handoff.serviceIdType'), body: t('docsHosterApi.handoff.serviceIdBody') },
            { term: 'target_path', badge: t('docsHosterApi.handoff.targetType'), body: t('docsHosterApi.handoff.targetBody') },
          ]}
        />
        <CodeBlock code={HANDOFF_COMMAND} label={t('docsHosterApi.handoff.commandLabel')} testId="hoster-handoff-command" />
        <div className="mt-6">
          <DefinitionTable
            rows={HANDOFF_FACTS.map(fact => ({
              term: t(`docsHosterApi.handoffFact.${fact}.term`),
              body: t(`docsHosterApi.handoffFact.${fact}.body`),
            }))}
          />
        </div>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.handoff.createOnClick')}</p>
        <aside className="mt-4 flex gap-3 rounded-xl border border-status-warning/30 bg-status-warning/10 p-4 text-status-warning">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" />
          <p className="text-sm leading-6">{t('docsHosterApi.handoff.splitHosting')}</p>
        </aside>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.handoff.oidc')}</p>
      </section>

      <section aria-labelledby="admin" className="mb-10">
        <SectionHeading id="admin" icon={<Table2 className="h-5 w-5" />} title={t('docsHosterApi.admin.title')} />
        <p className="mb-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.admin.intro')}</p>
        <div className="overflow-x-auto rounded-xl border border-outline-variant">
          <table className="w-full min-w-[40rem] border-collapse text-left text-sm">
            <thead className="bg-surface-container-highest">
              <tr>
                <th scope="col" className="px-4 py-2.5 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">{t('docsHosterApi.admin.colMethod')}</th>
                <th scope="col" className="px-4 py-2.5 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">{t('docsHosterApi.admin.colPath')}</th>
                <th scope="col" className="px-4 py-2.5 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">{t('docsHosterApi.admin.colPermission')}</th>
                <th scope="col" className="px-4 py-2.5 font-label-md text-xs uppercase tracking-wider text-on-surface-variant">{t('docsHosterApi.admin.colPurpose')}</th>
              </tr>
            </thead>
            <tbody>
              {ADMIN_ENDPOINTS.map(([method, path, permission, key]) => (
                <tr key={`${method} ${path}`} className="border-t border-outline-variant bg-surface-container">
                  <td className="px-4 py-2.5 font-mono text-xs text-on-surface">{method}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-on-surface">{`/api/hoster${path}`}</td>
                  <td className="px-4 py-2.5 font-mono text-xs text-on-surface-variant">{permission}</td>
                  <td className="px-4 py-2.5 text-on-surface-variant">{t(`docsHosterApi.admin.${key}`)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-4 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.admin.productRole')}</p>
        <p className="mt-3 max-w-3xl text-sm leading-6 text-on-surface-variant">{t('docsHosterApi.admin.openapi')}</p>
      </section>

      <section aria-labelledby="operations" className="mb-10">
        <SectionHeading id="operations" icon={<ShieldCheck className="h-5 w-5" />} title={t('docsHosterApi.operations.title')} />
        <DefinitionTable
          rows={(['serviceUser', 'customerRights', 'noDelete', 'audit', 'noSecrets'] as const).map(item => ({
            term: t(`docsHosterApi.operations.${item}.term`),
            body: t(`docsHosterApi.operations.${item}.body`),
          }))}
        />
        <Link to="/docs/self-hosting#hoster-integration" className="msm-btn-secondary mt-6 inline-flex items-center gap-2 px-4 py-2 text-sm">
          <Plug className="h-4 w-4" />
          {t('docsHosterApi.operations.setupLink')}
        </Link>
      </section>
    </main>
  )
}
