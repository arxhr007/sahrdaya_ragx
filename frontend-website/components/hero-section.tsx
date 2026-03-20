"use client"

import Link from "next/link"
import { useState } from "react"
import { HeroAnimation } from "@/components/hero-animation"

export function HeroSection() {
  const [showInfo, setShowInfo] = useState(false)

  return (
    <div className="flex w-full max-w-5xl flex-col items-center justify-center">
      <HeroAnimation onAnimationComplete={() => setShowInfo(true)} />

      <div
        id="about-project"
        className={`mt-6 md:mt-8 text-center max-w-2xl px-2 md:px-4 transition-all duration-700 ${
          showInfo ? "opacity-100 translate-y-0" : "opacity-0 translate-y-4"
        }`}
      >
        <h2 className="text-xl md:text-2xl font-semibold text-foreground">
          Ask anything about our college and get verified answers.
        </h2>
        <p className="mt-3 text-sm md:text-base text-muted-foreground">
          Powered by AI + data from the official college website. No more scrolling PDFs, guessing, or outdated info.
        </p>
        <p className="mt-2 text-xs md:text-sm text-amber-700">
          Currently under testing (v1). Some answers may be inaccurate or incomplete, and some information may be missing.
          Please review{" "}
          <Link href="/terms" className="underline underline-offset-4 hover:text-amber-800">
            Terms & Conditions
          </Link>
          .
        </p>
      </div>
    </div>
  )
}
