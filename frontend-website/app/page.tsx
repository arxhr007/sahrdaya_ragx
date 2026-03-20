import { AnimatedGrid } from "@/components/animated-grid"
import { HeroSection } from "@/components/hero-section"
import { Navbar } from "@/components/navbar"
import FooterTime from "@/components/footer-time"

export default function Page() {
  return (
    <main className="relative min-h-[100dvh] flex items-center justify-center px-3 py-16 md:px-4 md:py-20">
      <Navbar />
      <AnimatedGrid />

      <HeroSection />

      {/* Footer bar inspired by the OS-like design in the prompt */}
      <div className="fixed bottom-0 left-0 right-0 h-12 bg-white/50 backdrop-blur-sm border-t flex items-center justify-between px-3 md:px-6 text-xs text-muted-foreground z-50">
        <div className="flex items-center gap-4">
          <span className="flex items-center gap-1">
            <div className="w-3 h-3 bg-primary rounded-xs" />
            Skip & Start
          </span>
          <span className="hidden sm:flex items-center gap-1 opacity-60">Messenger</span>
        </div>
        <FooterTime />
      </div>
    </main>
  )
}
