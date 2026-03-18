"use client"

import Link from "next/link"
import { Button } from "@/components/ui/button"
import { MessageSquare } from "lucide-react"

export function Navbar() {
  return (
    <nav className="fixed top-0 left-0 right-0 h-16 bg-white/50 backdrop-blur-sm border-b flex items-center justify-between px-6 z-50">
      <div className="flex items-center gap-2 text-primary font-semibold">
        <span className="text-sm">RAGx</span>
      </div>

      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          className="text-primary hover:bg-primary/10 text-[11px] h-auto py-2 px-4 uppercase font-medium"
        >
          About
        </Button>
        <Button
          variant="ghost"
          className="text-primary hover:bg-primary/10 text-[11px] h-auto py-2 px-4 uppercase font-medium"
        >
          Features
        </Button>
        <Link href="/chat">
          <Button
            variant="outline"
            className="border-primary text-primary hover:bg-primary hover:text-primary-foreground hover:border-primary transition-all duration-300 text-[11px] py-2 px-4 h-auto uppercase bg-transparent font-medium"
          >
            <MessageSquare className="w-3 h-3 mr-1" />
            Chat
          </Button>
        </Link>
      </div>
    </nav>
  )
}
