"use client"

import { useSearchParams } from "next/navigation"
import { AnimatedGrid } from "@/components/animated-grid"
import { ChatInterface } from "@/components/chat-interface"
import { StatusIndicator } from "@/components/status-indicator"
import { Button } from "@/components/ui/button"
import FooterTime from "@/components/footer-time"
import Link from "next/link"
import {
  MessageSquare,
  ArrowLeft,
  Sparkles,
} from "lucide-react"

export default function ChatPage() {
  const searchParams = useSearchParams()
  const initialMessage = searchParams.get("q") || undefined

  return (
    <main className="relative h-screen overflow-hidden">
      <AnimatedGrid />

      {/* Header */}
      <header className="fixed top-0 left-0 right-0 h-16 bg-white/50 backdrop-blur-sm border-b flex items-center justify-between px-6 z-50">
        <div className="flex items-center gap-4">
          <Link href="/">
            <Button
              variant="ghost"
              size="sm"
              className="text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="w-4 h-4 mr-1" />
              Back
            </Button>
          </Link>
          <div className="flex items-center gap-2 text-primary font-semibold">
            <Sparkles className="w-4 h-4" />
            <span className="text-sm">RAGx Chat</span>
          </div>
        </div>
        <StatusIndicator />
      </header>

      {/* Main Content */}
      <div className="fixed top-16 bottom-12 left-0 right-0 px-4 md:px-8 py-4 overflow-hidden">
        <div className="max-w-7xl mx-auto h-full overflow-hidden">
          <div className="h-full overflow-hidden">
            <div className="h-full overflow-hidden">
              <ChatInterface initialMessage={initialMessage} />
            </div>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="fixed bottom-0 left-0 right-0 h-12 bg-white/50 backdrop-blur-sm border-t flex items-center justify-between px-6 text-xs text-muted-foreground z-50">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1">
            <MessageSquare className="w-3 h-3" />
            RAG Chat Interface
          </span>
        </div>
        <FooterTime />
      </div>
    </main>
  )
}
