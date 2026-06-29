import { Link } from "@tanstack/react-router"
import { Landmark } from "lucide-react"

import { cn } from "@/lib/utils"

interface LogoProps {
  variant?: "full" | "icon" | "responsive"
  className?: string
  asLink?: boolean
}

export function Logo({
  variant = "full",
  className,
  asLink = true,
}: LogoProps) {
  const content = (
    <span
      className={cn("flex items-center gap-2 text-lg font-bold", className)}
    >
      <Landmark className="size-6 shrink-0 text-indigo-500" />
      {variant !== "icon" && (
        <span
          className={
            variant === "responsive"
              ? "group-data-[collapsible=icon]:hidden"
              : undefined
          }
        >
          Ledger<span className="text-indigo-500">Flow</span>
        </span>
      )}
    </span>
  )

  if (!asLink) {
    return content
  }

  return <Link to="/">{content}</Link>
}
