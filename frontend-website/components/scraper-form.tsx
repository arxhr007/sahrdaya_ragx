"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Badge } from "@/components/ui/badge"
import { updateData, UpdateResponse } from "@/lib/api"
import { Globe, Loader2, CheckCircle2, AlertCircle, Link2, FileText, Layers } from "lucide-react"

interface ScraperFormProps {
  onSuccess?: (response: UpdateResponse) => void
}

export function ScraperForm({ onSuccess }: ScraperFormProps) {
  const [url, setUrl] = useState("")
  const [outputPrefix, setOutputPrefix] = useState("site_output")
  const [usePlaywright, setUsePlaywright] = useState(true)
  const [maxPages, setMaxPages] = useState(100)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [result, setResult] = useState<UpdateResponse | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!url.trim()) return

    setIsLoading(true)
    setError(null)
    setResult(null)

    try {
      const response = await updateData({
        url: url.trim(),
        output_prefix: outputPrefix,
        use_playwright: usePlaywright,
        max_pages: maxPages,
      })
      setResult(response)
      onSuccess?.(response)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to scrape URL")
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <div className="bg-white/50 backdrop-blur-sm rounded-xl border shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b bg-white/30">
        <div className="w-8 h-8 rounded-lg bg-primary/10 flex items-center justify-center">
          <Globe className="w-4 h-4 text-primary" />
        </div>
        <div>
          <h3 className="text-sm font-semibold text-foreground">Add Data Source</h3>
          <p className="text-[10px] text-muted-foreground">Scrape and index a website</p>
        </div>
      </div>

      {/* Form */}
      <form onSubmit={handleSubmit} className="p-4 space-y-4">
        <div className="space-y-2">
          <Label htmlFor="url" className="text-xs font-medium flex items-center gap-1">
            <Link2 className="w-3 h-3" />
            Website URL
          </Label>
          <Input
            id="url"
            type="url"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="https://example.com"
            className="bg-white/50 text-sm"
            disabled={isLoading}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="prefix" className="text-xs font-medium flex items-center gap-1">
            <FileText className="w-3 h-3" />
            Output Prefix
          </Label>
          <Input
            id="prefix"
            type="text"
            value={outputPrefix}
            onChange={(e) => setOutputPrefix(e.target.value)}
            placeholder="site_output"
            className="bg-white/50 text-sm"
            disabled={isLoading}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="maxPages" className="text-xs font-medium flex items-center gap-1">
            <Layers className="w-3 h-3" />
            Max Pages
          </Label>
          <Input
            id="maxPages"
            type="number"
            value={maxPages}
            onChange={(e) => setMaxPages(Number(e.target.value))}
            min={1}
            max={1000}
            className="bg-white/50 text-sm"
            disabled={isLoading}
          />
        </div>

        <div className="flex items-center justify-between py-2">
          <div className="space-y-0.5">
            <Label htmlFor="playwright" className="text-xs font-medium">
              Use Playwright
            </Label>
            <p className="text-[10px] text-muted-foreground">
              Better for JavaScript-heavy sites
            </p>
          </div>
          <Switch
            id="playwright"
            checked={usePlaywright}
            onCheckedChange={setUsePlaywright}
            disabled={isLoading}
          />
        </div>

        <Button
          type="submit"
          disabled={!url.trim() || isLoading}
          className="w-full bg-primary hover:bg-primary/90"
        >
          {isLoading ? (
            <>
              <Loader2 className="w-4 h-4 mr-2 animate-spin" />
              Scraping...
            </>
          ) : (
            <>
              <Globe className="w-4 h-4 mr-2" />
              Start Scraping
            </>
          )}
        </Button>
      </form>

      {/* Result */}
      {result && (
        <div className="px-4 py-3 bg-green-50/50 border-t border-green-200/50">
          <div className="flex items-start gap-2">
            <CheckCircle2 className="w-4 h-4 text-green-600 mt-0.5" />
            <div className="flex-1">
              <p className="text-xs font-medium text-green-700">{result.message}</p>
              <div className="flex gap-2 mt-2">
                <Badge variant="outline" className="text-[10px] bg-white/50">
                  {result.pages_scraped} pages
                </Badge>
                <Badge variant="outline" className="text-[10px] bg-white/50">
                  {result.chunks_created} chunks
                </Badge>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="px-4 py-3 bg-destructive/10 border-t border-destructive/20">
          <div className="flex items-start gap-2">
            <AlertCircle className="w-4 h-4 text-destructive mt-0.5" />
            <p className="text-xs text-destructive">{error}</p>
          </div>
        </div>
      )}
    </div>
  )
}
