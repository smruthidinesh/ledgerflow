import { createFileRoute } from "@tanstack/react-router"
import { ArrowRight, Cpu, Database, Radio, Repeat, Webhook } from "lucide-react"
import type { ReactNode } from "react"
import { useEffect, useState } from "react"

export const Route = createFileRoute("/_layout/events")({
  component: EventsPage,
  head: () => ({ meta: [{ title: "Events - LedgerFlow" }] }),
})

const BASE = import.meta.env.VITE_API_URL
const token = () => localStorage.getItem("access_token") || ""
const auth = { Authorization: `Bearer ${token()}` }

function EventsPage() {
  const [events, setEvents] = useState<any[]>([])
  const [stream, setStream] = useState<any>(null)

  async function refresh() {
    try {
      setEvents(await fetch(`${BASE}/api/v1/ledger/events?limit=40`, { headers: auth }).then((r) => r.json()))
      setStream(await fetch(`${BASE}/api/v1/ledger/stream-info`, { headers: auth }).then((r) => r.json()))
    } catch {
      /* transient */
    }
  }
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 2000)
    return () => clearInterval(id)
  }, [])

  const pending = events.filter((e) => e.status === "pending").length

  return (
    <div>
      <div className="flex items-center gap-3">
        <Radio className="h-7 w-7 text-indigo-400" />
        <div>
          <h1 className="text-2xl font-bold">Events</h1>
          <p className="text-sm text-muted-foreground">
            How LedgerFlow stays event-driven without ever losing an event. Watch a real event travel the pipeline below.
          </p>
        </div>
      </div>

      {/* the flow */}
      <div className="mt-6 overflow-x-auto rounded-2xl border bg-card p-6">
        <div className="flex min-w-max items-stretch gap-2">
          <Stage icon={Webhook} title="1 · API write" tone="indigo"
            body="A transfer writes the ledger entries AND an outbox row in one DB transaction." />
          <Arrow />
          <Stage icon={Database} title="2 · Outbox (Postgres)" tone="indigo"
            body="Event stored as status=pending in the same commit. If anything crashes now, it's still safe on disk." />
          <Arrow />
          <Stage icon={Repeat} title="3 · Relay polls" tone="amber"
            body="A background relay reads pending rows every 2s and publishes them to the bus, then marks them published." />
          <Arrow />
          <Stage icon={Radio} title="4 · Redis Stream" tone="emerald"
            body={`Appended to the "${stream?.stream || "ledger.events"}" stream — durable, ordered, replayable.`} />
          <Arrow />
          <Stage icon={Cpu} title="5 · Worker consumes" tone="emerald"
            body={`A consumer group (${stream?.consumers ?? 0} worker) reads & ACKs each event — at-least-once delivery.`} />
        </div>
        <p className="mt-4 text-xs text-muted-foreground">
          <b className="text-foreground">Why the outbox?</b> Writing the event in the same transaction as the money movement makes it
          impossible to commit a transfer without its event (or vice-versa). The relay decouples delivery, so a Redis outage delays
          events — it never loses them. This is the <i>transactional outbox</i> pattern.
        </p>
      </div>

      {/* live event log */}
      <div className="mt-8 mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
          Event log <span className="ml-1 text-xs normal-case text-emerald-400">● live from the outbox table</span>
        </h2>
        <span className="text-xs text-muted-foreground">
          {pending > 0 ? `${pending} pending → publishing…` : "all published"} · {stream?.length ?? 0} on stream
        </span>
      </div>
      <div className="overflow-hidden rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Created</th>
              <th className="px-4 py-2 font-medium">Event type</th>
              <th className="px-4 py-2 font-medium">Transaction</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {events.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">No events yet — make a transfer or load demo data on the Ledger page.</td></tr>
            )}
            {events.map((e) => (
              <tr key={e.id} className="border-t">
                <td className="px-4 py-2 text-muted-foreground">{e.created_at ? new Date(e.created_at).toLocaleTimeString() : "—"}</td>
                <td className="px-4 py-2 font-mono text-xs">{e.event_type}</td>
                <td className="px-4 py-2 font-mono text-xs text-muted-foreground">{e.aggregate_id.slice(0, 8)}</td>
                <td className="px-4 py-2">
                  {e.status === "published" ? (
                    <span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-400">published</span>
                  ) : (
                    <span className="rounded-md bg-amber-500/10 px-2 py-0.5 text-xs text-amber-400">pending</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const tones: Record<string, string> = {
  indigo: "border-indigo-500/30 bg-indigo-500/5",
  amber: "border-amber-500/30 bg-amber-500/5",
  emerald: "border-emerald-500/30 bg-emerald-500/5",
}

function Stage({ icon: Icon, title, body, tone }: { icon: any; title: string; body: ReactNode; tone: string }) {
  return (
    <div className={`w-52 shrink-0 rounded-xl border p-3 ${tones[tone]}`}>
      <div className="mb-1.5 flex items-center gap-2 text-sm font-semibold">
        <Icon className="h-4 w-4" /> {title}
      </div>
      <p className="text-xs text-muted-foreground">{body}</p>
    </div>
  )
}

function Arrow() {
  return (
    <div className="flex shrink-0 items-center text-muted-foreground">
      <ArrowRight className="h-5 w-5" />
    </div>
  )
}
