"use client"

import { useEffect, useRef } from "react"
import gsap from "gsap"
import { ScrollTrigger } from "gsap/ScrollTrigger"

gsap.registerPlugin(ScrollTrigger)

const SCROLL_TEXTS = [
  "Discover campus events and activities",
  "Connect with students and faculty",
  "Access academic resources instantly",
  "Get real-time campus updates",
  "Explore clubs and organizations",
]

const BANNER_TEXT = "SAHRDAYA RAGX • AI-POWERED CAMPUS ASSISTANT • ASK ANYTHING • "

export function ScrollSection() {
  const sectionRef = useRef<HTMLDivElement>(null)
  const rhombusRef = useRef<HTMLDivElement>(null)
  const textRef = useRef<HTMLDivElement>(null)
  const bannerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!sectionRef.current || !rhombusRef.current || !textRef.current) return

    // Trapezium reveal pulled from the left
    gsap.fromTo(
      rhombusRef.current,
      {
        x: "-10%",
        scaleX: 0,
        rotation: 0,
        transformOrigin: "left center",
      },
      {
        x: "0%",
        scaleX: 1,
        rotation: 0,
        transformOrigin: "left center",
        ease: "power3.out",
        scrollTrigger: {
          trigger: sectionRef.current,
          start: "top bottom",
          end: "center center",
          scrub: 1,
        },
      }
    )

    // Text animation from right
    gsap.fromTo(
      textRef.current,
      {
        x: "100%",
        opacity: 0,
      },
      {
        x: "0%",
        opacity: 1,
        scrollTrigger: {
          trigger: sectionRef.current,
          start: "top bottom",
          end: "center center",
          scrub: 1,
        },
      }
    )

    // Infinite banner scroll
    if (bannerRef.current) {
      gsap.to(bannerRef.current, {
        x: "-50%",
        duration: 20,
        repeat: -1,
        ease: "none",
      })
    }

    return () => {
      ScrollTrigger.getAll().forEach(trigger => trigger.kill())
    }
  }, [])

  return (
    <section
      ref={sectionRef}
      className="relative h-screen w-full flex flex-col items-center justify-center px-12 bg-white/90 overflow-hidden"
    >
      {/* Moving Banner Stripe */}
      <div className="absolute top-2/3 left-0 right-0 bg-primary py-4 rotate-3 overflow-hidden">
        <div ref={bannerRef} className="flex whitespace-nowrap">
          <span className="text-white font-bold text-2xl tracking-wider">
            {BANNER_TEXT + BANNER_TEXT}
          </span>
        </div>
      </div>

      <div className="flex items-center justify-between w-full z-10">
        {/* Left-pull trapezium reveal (formerly rhombus) */}
      <div
        ref={rhombusRef}
        className="relative w-64 h-64 md:w-96 md:h-96 overflow-hidden -ml-12 md:-ml-12"
      >
        <div className="absolute inset-0 bg-primary clip-trapezium-left-flipped" />
      </div>

      {/* Random text from right */}
      <div ref={textRef} className="max-w-xl text-right">
        <h2 className="text-3xl md:text-5xl font-bold text-primary mb-6">
          Everything You Need
        </h2>
        <div className="space-y-3 text-lg md:text-xl text-foreground/80">
          {SCROLL_TEXTS.map((text, index) => (
            <p key={index} className="opacity-80 hover:opacity-100 transition-opacity">
              {text}
            </p>
          ))}
        </div>
      </div>
      </div>
    </section>
  )
}
