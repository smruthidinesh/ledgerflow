import { Activity, BookText, Gauge, Home, Landmark, Users } from "lucide-react"

import { SidebarAppearance } from "@/components/Common/Appearance"
import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { User } from "./User"

const API_DOCS_URL = `${import.meta.env.VITE_API_URL}/docs`

const baseItems: Item[] = [
  { icon: Home, title: "Dashboard", path: "/" },
  { icon: Landmark, title: "Ledger", path: "/ledger" },
  // live, self-documenting FastAPI Swagger UI (opens in a new tab)
  { icon: BookText, title: "API Docs", path: API_DOCS_URL, external: true },
]

// Operator dashboards — system-wide views, superuser only
const operatorItems: Item[] = [
  { icon: Gauge, title: "Operations", path: "/operations" },
  { icon: Activity, title: "Events", path: "/events" },
  { icon: Users, title: "Admin", path: "/admin" },
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()

  const items = currentUser?.is_superuser
    ? [...baseItems, ...operatorItems]
    : baseItems

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="px-4 py-6 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
      </SidebarContent>
      <SidebarFooter>
        <SidebarAppearance />
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
