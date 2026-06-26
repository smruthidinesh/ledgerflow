import { createFileRoute } from "@tanstack/react-router"
import { ArrowRight, Landmark, Plus, Send } from "lucide-react"
import type { ReactNode } from "react"
import { useEffect, useState } from "react"

export const Route = createFileRoute("/_layout/ledger")({
  component: LedgerPage,
  head: () => ({ meta: [{ title: "Ledger - LedgerFlow" }] }),
})

const BASE = import.meta.env.VITE_API_URL

type Account = { id: string; name: string; currency: string; balance_cents: number }
type Recon = { global_drift_cents: number; balanced: boolean; unbalanced_transactions: string[] }

const token = () => localStorage.getItem("access_token") || ""

async function api(path: string, opts: RequestInit = {}) {
  const res = await fetch(`${BASE}/api/v1/ledger${path}`, {
    ...opts,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token()}`, ...(opts.headers as Record<string, string>) },
  })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`)
  return res.json()
}

const dollars = (c: number) => `$${(c / 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}`
const idem = () => crypto.randomUUID()

function LedgerPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [recon, setRecon] = useState<Recon | null>(null)
  const [msg, setMsg] = useState("")
  const [name, setName] = useState("")
  const [dep, setDep] = useState({ to: "", amount: "" })
  const [tr, setTr] = useState({ from: "", to: "", amount: "" })
  const [feed, setFeed] = useState<any[]>([])

  async function refresh() {
    try {
      setAccounts(await api("/accounts"))
      setRecon(await api("/reconciliation"))
      setFeed(await api("/transactions?limit=25"))
    } catch (e: any) {
      setMsg(`⚠ ${e.message}`)
    }
  }
  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 3000) // live: feed + balances update on their own
    return () => clearInterval(id)
  }, [])

  async function run(fn: () => Promise<unknown>, ok: string) {
    setMsg("")
    try {
      await fn()
      setMsg(ok)
      await refresh()
    } catch (e: any) {
      setMsg(`⚠ ${e.message}`)
    }
  }

  const visible = accounts.filter((a) => !a.name.includes(":"))

  return (
    <div className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex items-center gap-3">
        <Landmark className="h-7 w-7 text-indigo-400" />
        <div>
          <h1 className="text-2xl font-bold">Ledger</h1>
          <p className="text-sm text-muted-foreground">
            Create accounts, deposit, and transfer — every move is balanced double-entry bookkeeping.
          </p>
        </div>
      </div>

      {recon && (
        <div
          className={`mt-5 rounded-xl border px-4 py-2.5 text-sm ${
            recon.balanced ? "border-emerald-500/40 bg-emerald-500/10" : "border-red-500/40 bg-red-500/10"
          }`}
        >
          Reconciliation:{" "}
          <b className={recon.balanced ? "text-emerald-400" : "text-red-400"}>
            {recon.balanced ? "BALANCED ✓" : "DRIFT!"}
          </b>{" "}
          <span className="text-muted-foreground">· global drift {recon.global_drift_cents} cents · the whole ledger nets to zero</span>
        </div>
      )}
      {msg && <div className="mt-3 text-sm text-indigo-400">{msg}</div>}

      {/* accounts */}
      <h2 className="mt-8 mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">Accounts</h2>
      <div className="overflow-hidden rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">Account</th>
              <th className="px-4 py-2 text-right font-medium">Balance</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr><td colSpan={2} className="px-4 py-6 text-center text-muted-foreground">No accounts yet — create one below.</td></tr>
            )}
            {visible.map((a) => (
              <tr key={a.id} className="border-t">
                <td className="px-4 py-2.5">{a.name}</td>
                <td className="px-4 py-2.5 text-right font-mono tabular-nums">{dollars(a.balance_cents)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {/* actions */}
      <div className="mt-6 grid gap-4 md:grid-cols-3">
        <Card title="New account" icon={Plus}>
          <input className={inp} placeholder="Account name" value={name} onChange={(e) => setName(e.target.value)} />
          <button className={btn} onClick={() => run(() => api("/accounts", { method: "POST", body: JSON.stringify({ name }) }).then(() => setName("")), "account created")}>
            Create account
          </button>
        </Card>

        <Card title="Deposit" icon={ArrowRight}>
          <Select value={dep.to} onChange={(v) => setDep({ ...dep, to: v })} accounts={visible} placeholder="To account" />
          <input className={inp} placeholder="Amount ($)" value={dep.amount} onChange={(e) => setDep({ ...dep, amount: e.target.value })} />
          <button className={btn} onClick={() => run(() => api("/deposit", { method: "POST", headers: { "Idempotency-Key": idem() }, body: JSON.stringify({ to_account_id: dep.to, amount_cents: Math.round(parseFloat(dep.amount) * 100) }) }), "deposited")}>
            Deposit
          </button>
        </Card>

        <Card title="Transfer" icon={Send}>
          <Select value={tr.from} onChange={(v) => setTr({ ...tr, from: v })} accounts={visible} placeholder="From" />
          <Select value={tr.to} onChange={(v) => setTr({ ...tr, to: v })} accounts={visible} placeholder="To" />
          <input className={inp} placeholder="Amount ($)" value={tr.amount} onChange={(e) => setTr({ ...tr, amount: e.target.value })} />
          <button className={btn} onClick={() => run(() => api("/transfers", { method: "POST", headers: { "Idempotency-Key": idem() }, body: JSON.stringify({ from_account_id: tr.from, to_account_id: tr.to, amount_cents: Math.round(parseFloat(tr.amount) * 100) }) }), "transferred")}>
            Transfer
          </button>
        </Card>
      </div>

      {/* live activity feed */}
      <h2 className="mt-8 mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">
        Activity <span className="ml-1 text-xs normal-case text-emerald-400">● live</span>
      </h2>
      <div className="overflow-hidden rounded-xl border">
        <table className="w-full text-sm">
          <thead className="bg-muted/40 text-left text-muted-foreground">
            <tr>
              <th className="px-4 py-2 font-medium">When</th>
              <th className="px-4 py-2 font-medium">From → To</th>
              <th className="px-4 py-2 text-right font-medium">Amount</th>
              <th className="px-4 py-2 font-medium">Status</th>
            </tr>
          </thead>
          <tbody>
            {feed.length === 0 && (
              <tr><td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">No activity yet.</td></tr>
            )}
            {feed.map((f) => (
              <tr key={f.id} className="border-t">
                <td className="px-4 py-2 text-muted-foreground">{new Date(f.created_at).toLocaleTimeString()}</td>
                <td className="px-4 py-2">{f.from_account} <span className="text-muted-foreground">→</span> {f.to_account}</td>
                <td className="px-4 py-2 text-right font-mono tabular-nums">{dollars(f.amount_cents)}</td>
                <td className="px-4 py-2"><span className="rounded-md bg-emerald-500/10 px-2 py-0.5 text-xs text-emerald-400">{f.status}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

const inp = "mb-2 w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-indigo-500"
const btn = "w-full rounded-lg bg-indigo-500 px-3 py-2 text-sm font-medium text-white transition hover:bg-indigo-400"

function Card({ title, icon: Icon, children }: { title: string; icon: any; children: ReactNode }) {
  return (
    <div className="rounded-2xl border bg-card p-4">
      <div className="mb-3 flex items-center gap-2 font-semibold">
        <Icon className="h-4 w-4 text-indigo-400" /> {title}
      </div>
      {children}
    </div>
  )
}

function Select({ value, onChange, accounts, placeholder }: { value: string; onChange: (v: string) => void; accounts: Account[]; placeholder: string }) {
  return (
    <select className={inp} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">{placeholder}</option>
      {accounts.map((a) => (
        <option key={a.id} value={a.id}>{a.name} ({dollars(a.balance_cents)})</option>
      ))}
    </select>
  )
}
