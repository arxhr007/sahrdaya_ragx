"use client"

import { useState, useRef, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { sendMessage, clearSession, createSession, ApiError } from "@/lib/api"
import { Send, Trash2, Loader2, Bot, User, Sparkles } from "lucide-react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"

interface Message {
  role: "user" | "assistant"
  content: string
  timestamp: Date
}

interface ChatInterfaceProps {
  sessionId?: string
  initialMessage?: string
}

// Extract image URLs from message content
function extractImageUrls(content: string): string[] {
  const imageUrls: string[] = []
  
  // Match markdown image syntax: ![alt](url)
  const markdownRegex = /!\[.*?\]\((.*?)\)/g
  let match: RegExpExecArray | null
  while ((match = markdownRegex.exec(content)) !== null) {
    imageUrls.push(match[1])
  }
  
  // Match plain URLs to images
  const urlRegex = /(https?:\/\/[^\s<>"{}|\\^`\[\]]*\.(?:jpg|jpeg|png|gif|webp|svg|bmp))/gi
  while ((match = urlRegex.exec(content)) !== null) {
    if (!imageUrls.includes(match[1])) {
      imageUrls.push(match[1])
    }
  }
  
  return imageUrls
}

export function ChatInterface({ sessionId = "default", initialMessage }: ChatInterfaceProps) {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [isLoading, setIsLoading] = useState(false)
  const [isCreatingSession, setIsCreatingSession] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [activeSessionId, setActiveSessionId] = useState(sessionId || "")
  const scrollContainerRef = useRef<HTMLDivElement>(null)
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const initialMessageSent = useRef(false)

  const ensureSession = useCallback(async () => {
    if (activeSessionId && activeSessionId !== "default") {
      return activeSessionId
    }

    setIsCreatingSession(true)
    const created = await createSession()
    setActiveSessionId(created.session_id)
    setIsCreatingSession(false)
    return created.session_id
  }, [activeSessionId])

  useEffect(() => {
    let cancelled = false

    async function bootSession() {
      if (sessionId && sessionId !== "default") {
        setActiveSessionId(sessionId)
        setIsCreatingSession(false)
        return
      }

      try {
        setIsCreatingSession(true)
        const created = await createSession()
        if (!cancelled) {
          setActiveSessionId(created.session_id)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to create chat session")
        }
      } finally {
        if (!cancelled) {
          setIsCreatingSession(false)
        }
      }
    }

    bootSession()

    return () => {
      cancelled = true
    }
  }, [sessionId])

  const scrollToBottom = useCallback(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, scrollToBottom])

  // Send a message programmatically
  const sendMessageToChat = useCallback(async (messageContent: string) => {
    if (!messageContent.trim() || isLoading || isCreatingSession) return

    const userMessage: Message = {
      role: "user",
      content: messageContent.trim(),
      timestamp: new Date(),
    }

    setMessages((prev) => [...prev, userMessage])
    setIsLoading(true)
    setError(null)

    try {
      const currentSessionId = await ensureSession()

      const response = await sendMessage({
        message: userMessage.content,
        session_id: currentSessionId,
        include_metadata: true,
      })

      const assistantMessage: Message = {
        role: "assistant",
        content: response.answer,
        timestamp: new Date(),
      }

      setMessages((prev) => [...prev, assistantMessage])
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        try {
          const created = await createSession()
          setActiveSessionId(created.session_id)
          setError("Session expired. A new session was created. Please resend your message.")
        } catch {
          setError("Session expired and a new session could not be created.")
        }
      } else if (err instanceof ApiError && err.status === 429) {
        const secs = err.retryAfterSeconds
        setError(secs ? `Rate limited. Try again in ${secs}s.` : "Rate limited. Please try again shortly.")
      } else {
        setError(err instanceof Error ? err.message : "Failed to send message")
      }
    } finally {
      setIsLoading(false)
      inputRef.current?.focus()
    }
  }, [ensureSession, isCreatingSession, isLoading])

  // Handle initial message from URL query param
  useEffect(() => {
    if (initialMessage && !initialMessageSent.current && !isCreatingSession && activeSessionId) {
      initialMessageSent.current = true
      sendMessageToChat(initialMessage)
    }
  }, [activeSessionId, initialMessage, isCreatingSession, sendMessageToChat])

  const handleSend = async () => {
    if (!input.trim() || isLoading) return
    await sendMessageToChat(input.trim())
    setInput("")
  }

  const handleClearChat = async () => {
    if (!activeSessionId) return

    try {
      await clearSession(activeSessionId)
      const created = await createSession()
      setActiveSessionId(created.session_id)
      setMessages([])
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to clear chat")
    }
  }

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  return (
    <div className="flex flex-col h-full max-h-full bg-white/50 backdrop-blur-sm rounded-xl border shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex-shrink-0 flex items-center justify-between px-4 py-3 border-b bg-white/30">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
            <Sparkles className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-foreground">RAG Assistant</h3>
            <p className="text-[10px] text-muted-foreground">Powered by AI</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="text-[10px]">
            {messages.length} messages
          </Badge>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={handleClearChat}
            className="text-muted-foreground hover:text-destructive"
          >
            <Trash2 className="w-4 h-4" />
          </Button>
        </div>
      </div>

      {/* Messages */}
      <div 
        ref={scrollContainerRef}
        className="flex-1 min-h-0 overflow-y-auto p-4 scroll-smooth"
      >
        <div className="space-y-4">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-[300px] text-center">
              <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
                <Bot className="w-8 h-8 text-primary" />
              </div>
              <h4 className="text-sm font-medium text-foreground mb-1">Start a conversation</h4>
              <p className="text-xs text-muted-foreground max-w-[250px]">
                Ask me anything about the data that has been indexed. I&apos;ll help you find the information you need.
              </p>
            </div>
          )}

          {messages.map((message, index) => (
            <div
              key={index}
              className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}
            >
              {message.role === "assistant" && (
                <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0 mt-0.5">
                  <Bot className="w-4 h-4 text-primary" />
                </div>
              )}
              <div
                className={`max-w-[80%] rounded-xl px-4 py-2.5 ${
                  message.role === "user"
                    ? "bg-primary text-primary-foreground"
                    : "w-fit max-w-full bg-muted/50 text-foreground border"
                }`}
              >
                {message.role === "assistant" ? (
                  <>
                    <div className="prose prose-sm dark:prose-invert max-w-none text-sm overflow-x-auto [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_p]:my-2 [&_ul]:my-2 [&_ol]:my-2 [&_li]:my-0.5 [&_pre]:my-2 [&_pre]:bg-black/10 [&_pre]:p-2 [&_pre]:rounded-lg [&_code]:text-xs [&_code]:bg-black/10 [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm [&_h1]:font-semibold [&_h2]:font-semibold [&_h3]:font-medium [&_a]:text-primary [&_a]:underline [&_blockquote]:border-l-2 [&_blockquote]:border-primary/50 [&_blockquote]:pl-3 [&_blockquote]:italic [&_table]:text-xs [&_table]:w-max [&_table]:min-w-full [&_th]:p-2 [&_td]:p-2 [&_th]:bg-muted/50 [&_tr]:border-b [&_tr]:border-border">
                      <ReactMarkdown remarkPlugins={[remarkGfm]}>
                        {message.content}
                      </ReactMarkdown>
                    </div>
                    {extractImageUrls(message.content).length > 0 && (
                      <div className="mt-3 space-y-2 flex flex-col">
                        {extractImageUrls(message.content).map((imageUrl, idx) => (
                          <img
                            key={idx}
                            src={imageUrl}
                            alt={`Response image ${idx + 1}`}
                            className="max-w-sm rounded-lg border border-border/50 max-h-96 object-cover"
                            onError={(e) => {
                              ;(e.target as HTMLImageElement).style.display = "none"
                            }}
                          />
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                )}
                <p className="text-[9px] mt-1 opacity-60">
                  {message.timestamp.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                </p>
              </div>
              {message.role === "user" && (
                <div className="w-7 h-7 rounded-lg bg-primary flex items-center justify-center flex-shrink-0 mt-0.5">
                  <User className="w-4 h-4 text-primary-foreground" />
                </div>
              )}
            </div>
          ))}

          {isLoading && (
            <div className="flex gap-3 justify-start">
              <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center flex-shrink-0">
                <Bot className="w-4 h-4 text-primary" />
              </div>
              <div className="bg-muted/50 border rounded-xl px-4 py-3">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin text-primary" />
                  <span className="text-xs text-muted-foreground">Thinking...</span>
                </div>
              </div>
            </div>
          )}
          
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex-shrink-0 px-4 py-2 bg-destructive/10 border-t border-destructive/20">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Input */}
      <div className="flex-shrink-0 p-4 border-t bg-white/30">
        <div className="flex gap-2">
          <Input
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyPress}
            placeholder="Type your message..."
            disabled={isLoading || isCreatingSession}
            className="flex-1 bg-white/50 text-sm"
          />
          <Button
            onClick={handleSend}
            disabled={!input.trim() || isLoading || isCreatingSession}
            size="icon"
            className="bg-primary hover:bg-primary/90"
          >
            {isLoading ? (
              <Loader2 className="w-4 h-4 animate-spin" />
            ) : (
              <Send className="w-4 h-4" />
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}
