"use client"

import { useState } from "react"
import { HeroAnimation } from "@/components/hero-animation"

export function HeroSection() {
  const [showInfo, setShowInfo] = useState(false)

  return (
    <div className="flex flex-col items-center justify-center">
      <HeroAnimation onAnimationComplete={() => setShowInfo(true)} />

      <div
        className={`mt-8 text-center max-w-2xl px-4 transition-all duration-700 ${
          showInfo ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
        }`}
      >
        <h2 className="text-xl md:text-2xl font-semibold text-foreground">
          Ask anything about our college and get verified answers.
        </h2>
        <p className="mt-3 text-sm md:text-base text-muted-foreground">
          Powered by AI + official college documents. No more scrolling PDFs, guessing, or outdated info.
        </p>
      </div>
    </div>
  )
}
