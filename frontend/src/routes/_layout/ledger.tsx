import { createFileRoute } from "@tanstack/react-router"
import type { CSSProperties, ReactNode } from "react"
import { useEffect, useState } from "react"

export const Route = createFileRoute("/_layout/ledger")({
  component: LedgerPage,
  head: () => ({ meta: [{ title: "Ledger - LedgerFlow" }] }),
})

const BASE = import.meta.env.VITE_API_URL

type Account = { id: string; name: string; currency: string; balance_cents: number }
type Recon = { global_drift_cents: number; balanced: boolean; unbalanced_transactions: string[] }

function token() {
  return localStorage.getItem("access_token") || ""
}

async function api(path: string, opts: RequestInit = {}) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token()}`,
    ...(opts.headers as Record<string, string>),
  }
  const res = await fetch(`${BASE}/api/v1/ledger${path}`, { ...opts, headers })
  if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || `HTTP ${res.status}`)
  return res.json()
}

const dollars = (cents: number) => `$${(cents / 100).toFixed(2)}`

function LedgerPage() {
  const [accounts, setAccounts] = useState<Account[]>([])
  const [recon, setRecon] = useState<Recon | null>(null)
  const [msg, setMsg] = useState<string>("")
  const [name, setName] = useState("")
  const [dep, setDep] = useState({ to: "", amount: "" })
  const [tr, setTr] = useState({ from: "", to: "", amount: "" })

  async function refresh() {
    try {
      setAccounts(await api("/accounts"))
      setRecon(await api("/reconciliation"))
    } catch (e: any) {
      setMsg(e.message)
    }
  }
  useEffect(() => {
    refresh()
  }, [])

  async function run(fn: () => Promise<any>, ok: string) {
    setMsg("")
    try {
      await fn()
      setMsg(ok)
      await refresh()
    } catch (e: any) {
      setMsg(`⚠ ${e.message}`)
    }
  }

  const idem = () => crypto.randomUUID()

  return (
    <div style={{ maxWidth: 920, margin: "0 auto", padding: 24 }}>
      <h1 style={{ fontSize: 24, fontWeight: 600, marginBottom: 4 }}>LedgerFlow</h1>
      <p style={{ color: "#64748b", marginBottom: 16 }}>
        Immutable double-entry ledger · idempotent transfers · SAGA · outbox
      </p>

      {recon && (
        <div
          style={{
            padding: "8px 12px", borderRadius: 8, marginBottom: 16,
            background: recon.balanced ? "#052e1a" : "#3b0a0a",
            border: `1px solid ${recon.balanced ? "#10b981" : "#ef4444"}`,
          }}
        >
          Reconciliation: <b style={{ color: recon.balanced ? "#34d399" : "#f87171" }}>
            {recon.balanced ? "BALANCED ✓" : "DRIFT!"}
          </b>{" "}
          · global drift {recon.global_drift_cents} cents
        </div>
      )}
      {msg && <div style={{ marginBottom: 12, color: "#93c5fd" }}>{msg}</div>}

      <h2 style={{ fontWeight: 600, margin: "12px 0" }}>Accounts</h2>
      <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 16 }}>
        <thead>
          <tr style={{ textAlign: "left", color: "#94a3b8", fontSize: 13 }}>
            <th style={{ padding: 6 }}>Name</th><th style={{ padding: 6 }}>Balance</th>
          </tr>
        </thead>
        <tbody>
          {accounts.map((a) => (
            <tr key={a.id} style={{ borderTop: "1px solid #1e293b" }}>
              <td style={{ padding: 6 }}>{a.name}</td>
              <td style={{ padding: 6, fontVariantNumeric: "tabular-nums" }}>{dollars(a.balance_cents)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 }}>
        {/* Create account */}
        <Card title="New account">
          <input placeholder="name" value={name} onChange={(e) => setName(e.target.value)} style={inp} />
          <button style={btn} onClick={() => run(() => api("/accounts", { method: "POST", body: JSON.stringify({ name }) }).then(() => setName("")), "account created")}>
            Create
          </button>
        </Card>

        {/* Deposit */}
        <Card title="Deposit">
          <Select value={dep.to} onChange={(v) => setDep({ ...dep, to: v })} accounts={accounts} placeholder="to account" />
          <input placeholder="amount $" value={dep.amount} onChange={(e) => setDep({ ...dep, amount: e.target.value })} style={inp} />
          <button style={btn} onClick={() => run(() => api("/deposit", { method: "POST", headers: { "Idempotency-Key": idem() }, body: JSON.stringify({ to_account_id: dep.to, amount_cents: Math.round(parseFloat(dep.amount) * 100) }) }), "deposited")}>
            Deposit
          </button>
        </Card>

        {/* Transfer */}
        <Card title="Transfer">
          <Select value={tr.from} onChange={(v) => setTr({ ...tr, from: v })} accounts={accounts} placeholder="from" />
          <Select value={tr.to} onChange={(v) => setTr({ ...tr, to: v })} accounts={accounts} placeholder="to" />
          <input placeholder="amount $" value={tr.amount} onChange={(e) => setTr({ ...tr, amount: e.target.value })} style={inp} />
          <button style={btn} onClick={() => run(() => api("/transfers", { method: "POST", headers: { "Idempotency-Key": idem() }, body: JSON.stringify({ from_account_id: tr.from, to_account_id: tr.to, amount_cents: Math.round(parseFloat(tr.amount) * 100) }) }), "transferred")}>
            Transfer
          </button>
        </Card>
      </div>
    </div>
  )
}

const inp: CSSProperties = { width: "100%", padding: 8, marginBottom: 8, borderRadius: 6, border: "1px solid #334155", background: "#0b1220", color: "#e2e8f0" }
const btn: CSSProperties = { width: "100%", padding: 8, borderRadius: 6, background: "#6366f1", color: "white", border: "none", cursor: "pointer" }

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div style={{ border: "1px solid #1e293b", borderRadius: 10, padding: 12 }}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  )
}

function Select({ value, onChange, accounts, placeholder }: { value: string; onChange: (v: string) => void; accounts: Account[]; placeholder: string }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)} style={inp}>
      <option value="">{placeholder}</option>
      {accounts.filter((a) => !a.name.includes(":")).map((a) => (
        <option key={a.id} value={a.id}>{a.name} ({dollars(a.balance_cents)})</option>
      ))}
    </select>
  )
}
