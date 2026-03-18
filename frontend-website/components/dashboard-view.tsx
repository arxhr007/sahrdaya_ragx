"use client"

import { useState, useEffect, useCallback } from "react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog"
import { getDashboard, deleteSite, reloadRag, updateData, DashboardResponse, SiteInfo } from "@/lib/api"
import {
  Database,
  Globe,
  Trash2,
  RefreshCw,
  Loader2,
  FileText,
  Clock,
  Hash,
  ExternalLink,
  AlertTriangle,
  Layers,
  RotateCw,
} from "lucide-react"

interface DashboardViewProps {
  refreshTrigger?: number
}

export function DashboardView({ refreshTrigger }: DashboardViewProps) {
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<SiteInfo | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)
  const [isReloading, setIsReloading] = useState(false)
  const [updatingUrls, setUpdatingUrls] = useState<Set<string>>(new Set())

  const fetchDashboard = useCallback(async () => {
    setIsLoading(true)
    setError(null)
    try {
      const response = await getDashboard()
      setData(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to fetch dashboard")
    } finally {
      setIsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchDashboard()
  }, [fetchDashboard, refreshTrigger])

  const handleDelete = async () => {
    if (!deleteTarget) return
    setIsDeleting(true)
    try {
      await deleteSite(deleteTarget.url)
      setDeleteTarget(null)
      fetchDashboard()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete site")
    } finally {
      setIsDeleting(false)
    }
  }

  const handleReload = async () => {
    setIsReloading(true)
    try {
      await reloadRag()
      fetchDashboard()
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to reload RAG")
    } finally {
      setIsReloading(false)
    }
  }

  const handleUpdateSite = async (site: SiteInfo) => {
    setUpdatingUrls((prev) => new Set(prev).add(site.url))
    setError(null)
    try {
      await updateData({
        url: site.url,
        use_playwright: true,
        max_pages: 100,
      })
      fetchDashboard()
    } catch (err) {
      setError(err instanceof Error ? err.message : `Failed to update ${site.url}`)
    } finally {
      setUpdatingUrls((prev) => {
        const next = new Set(prev)
        next.delete(site.url)
        return next
      })
    }
  }

  const formatDate = (dateStr: string) => {
    if (!dateStr) return "Unknown"
    const date = new Date(dateStr)
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    })
  }

  const formatNumber = (num: number) => {
    return new Intl.NumberFormat().format(num)
  }

  return (
    <div className="bg-white/50 backdrop-blur-sm rounded-xl border shadow-sm overflow-hidden h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b bg-white/30">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
            <Database className="w-4 h-4 text-primary" />
          </div>
          <div>
            <h3 className="text-sm font-semibold text-foreground">Data Sources</h3>
            <p className="text-[10px] text-muted-foreground">
              {data ? `${data.total_sites} sites, ${formatNumber(data.total_chunks)} chunks` : "Loading..."}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={fetchDashboard}
            disabled={isLoading}
            className="text-muted-foreground hover:text-foreground"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? "animate-spin" : ""}`} />
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleReload}
            disabled={isReloading}
            className="text-xs h-8"
          >
            {isReloading ? (
              <Loader2 className="w-3 h-3 mr-1 animate-spin" />
            ) : (
              <Layers className="w-3 h-3 mr-1" />
            )}
            Reload RAG
          </Button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="px-4 py-2 bg-destructive/10 border-b border-destructive/20">
          <p className="text-xs text-destructive">{error}</p>
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {isLoading && !data ? (
          <div className="flex items-center justify-center h-[200px]">
            <Loader2 className="w-6 h-6 animate-spin text-primary" />
          </div>
        ) : data?.sites.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-[200px] text-center px-4">
            <div className="w-12 h-12 rounded-xl bg-muted/50 flex items-center justify-center mb-3">
              <Globe className="w-6 h-6 text-muted-foreground" />
            </div>
            <h4 className="text-sm font-medium text-foreground mb-1">No data sources</h4>
            <p className="text-xs text-muted-foreground">
              Add a website using the form to start indexing data
            </p>
          </div>
        ) : (
          <div className="p-4 space-y-3">
            {data?.sites.map((site) => (
              <div
                key={site.url}
                className="p-3 rounded-lg border bg-white/30 hover:bg-white/50 transition-colors group"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Globe className="w-3.5 h-3.5 text-primary flex-shrink-0" />
                      <a
                        href={site.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-medium text-foreground hover:text-primary truncate flex items-center gap-1"
                      >
                        {site.title || new URL(site.url).hostname}
                        <ExternalLink className="w-3 h-3 opacity-0 group-hover:opacity-100 transition-opacity" />
                      </a>
                    </div>
                    {site.description && (
                      <p className="text-[11px] text-muted-foreground line-clamp-2 mb-2 pl-5.5">
                        {site.description}
                      </p>
                    )}
                    <div className="flex flex-wrap gap-1.5 pl-5.5">
                      <Badge variant="outline" className="text-[9px] h-5 bg-white/50">
                        <Hash className="w-2.5 h-2.5 mr-0.5" />
                        {site.chunk_count} chunks
                      </Badge>
                      <Badge variant="outline" className="text-[9px] h-5 bg-white/50">
                        <FileText className="w-2.5 h-2.5 mr-0.5" />
                        {formatNumber(site.word_count)} words
                      </Badge>
                      <Badge variant="outline" className="text-[9px] h-5 bg-white/50">
                        <Clock className="w-2.5 h-2.5 mr-0.5" />
                        {formatDate(site.scraped_at)}
                      </Badge>
                    </div>
                  </div>
                  <div className="flex flex-col gap-1">
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => handleUpdateSite(site)}
                      disabled={updatingUrls.has(site.url)}
                      className="text-muted-foreground hover:text-primary opacity-0 group-hover:opacity-100 transition-opacity"
                      title="Re-scrape this URL"
                    >
                      {updatingUrls.has(site.url) ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <RotateCw className="w-4 h-4" />
                      )}
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      onClick={() => setDeleteTarget(site)}
                      disabled={updatingUrls.has(site.url)}
                      className="text-muted-foreground hover:text-destructive opacity-0 group-hover:opacity-100 transition-opacity"
                      title="Delete this source"
                    >
                      <Trash2 className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Last Updated */}
      {data?.last_updated && (
        <div className="px-4 py-2 border-t bg-muted/20">
          <p className="text-[10px] text-muted-foreground flex items-center gap-1">
            <Clock className="w-3 h-3" />
            Last updated: {formatDate(data.last_updated)}
          </p>
        </div>
      )}

      {/* Delete Dialog */}
      <Dialog open={!!deleteTarget} onOpenChange={() => setDeleteTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2 text-base">
              <AlertTriangle className="w-5 h-5 text-destructive" />
              Delete Data Source
            </DialogTitle>
            <DialogDescription className="text-sm">
              Are you sure you want to remove this data source?
            </DialogDescription>
          </DialogHeader>
          {deleteTarget && (
            <div className="py-2">
              <div className="p-3 rounded-lg bg-muted/50 border">
                <p className="text-sm font-medium truncate">{deleteTarget.title || deleteTarget.url}</p>
                <p className="text-xs text-muted-foreground truncate">{deleteTarget.url}</p>
              </div>
              <p className="text-xs text-muted-foreground mt-2">
                This will remove the site from tracking. You&apos;ll need to reload the RAG system for changes to take effect.
              </p>
            </div>
          )}
          <DialogFooter>
            <Button variant="outline" onClick={() => setDeleteTarget(null)} disabled={isDeleting}>
              Cancel
            </Button>
            <Button variant="destructive" onClick={handleDelete} disabled={isDeleting}>
              {isDeleting ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Deleting...
                </>
              ) : (
                <>
                  <Trash2 className="w-4 h-4 mr-2" />
                  Delete
                </>
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
