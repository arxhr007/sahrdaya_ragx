"use client"

import { useState, useEffect, useCallback } from "react"
import { Badge } from "@/components/ui/badge"
import { healthCheck, HealthResponse } from "@/lib/api"
import { Circle, Wifi, WifiOff } from "lucide-react"

export function StatusIndicator() {
  const [status, setStatus] = useState<HealthResponse | null>(null)
  const [isOnline, setIsOnline] = useState(false)
  const [isChecking, setIsChecking] = useState(true)

  const checkHealth = useCallback(async () => {
    try {
      const response = await healthCheck()
      setStatus(response)
      setIsOnline(true)
    } catch {
      setStatus(null)
      setIsOnline(false)
    } finally {
      setIsChecking(false)
    }
  }, [])

  useEffect(() => {
    checkHealth()
    const interval = setInterval(checkHealth, 30000) // Check every 30 seconds
    return () => clearInterval(interval)
  }, [checkHealth])

  if (isChecking) {
    return (
      <Badge variant="outline" className="text-[10px] bg-muted/50 animate-pulse">
        <Circle className="w-2 h-2 mr-1 fill-muted-foreground text-muted-foreground" />
        Connecting...
      </Badge>
    )
  }

  if (!isOnline) {
    return (
      <Badge variant="outline" className="text-[10px] bg-destructive/10 text-destructive border-destructive/30">
        <WifiOff className="w-3 h-3 mr-1" />
        Offline
      </Badge>
    )
  }

  return (
    <div className="flex items-center gap-2">
      <Badge variant="outline" className="text-[10px] bg-green-50 text-green-700 border-green-200">
        <Wifi className="w-3 h-3 mr-1" />
        Online
      </Badge>
      {status && (
        <Badge
          variant="outline"
          className={`text-[10px] ${
            status.rag_loaded
              ? "bg-green-50 text-green-700 border-green-200"
              : "bg-yellow-50 text-yellow-700 border-yellow-200"
          }`}
        >
          <Circle
            className={`w-2 h-2 mr-1 ${
              status.rag_loaded ? "fill-green-500 text-green-500" : "fill-yellow-500 text-yellow-500"
            }`}
          />
          RAG {status.rag_loaded ? "Ready" : "Empty"}
        </Badge>
      )}
    </div>
  )
}
