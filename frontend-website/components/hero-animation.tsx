"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import gsap from "gsap"
import { Button } from "@/components/ui/button"
import { Send } from "lucide-react"

const QUESTIONS = [
  "What are the hostel facilities available?",
  "How do I apply for scholarships?",
  "What is the campus placement record?",
  "Where is the library located?",
  "What sports facilities are available?",
  "How can I contact the admissions office?",
]

export function HeroAnimation({ onAnimationComplete }: { onAnimationComplete?: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const ragxRef = useRef<HTMLSpanElement>(null)
  const sahrdayaRef = useRef<HTMLSpanElement>(null)
  const askBarRef = useRef<HTMLDivElement>(null)
  const buttonsRef = useRef<HTMLDivElement>(null)
  const [placeholder, setPlaceholder] = useState(QUESTIONS[0])
  const [inputValue, setInputValue] = useState("")
  const router = useRouter()

  const handleSubmit = () => {
    if (inputValue.trim()) {
      router.push(`/chat?q=${encodeURIComponent(inputValue.trim())}`)
    } else {
      router.push('/chat')
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      e.preventDefault()
      handleSubmit()
    }
  }

  useEffect(() => {
    const tl = gsap.timeline({ 
      defaults: { ease: "power3.out" },
      onComplete: () => onAnimationComplete?.()
    })

    gsap.set(sahrdayaRef.current, { opacity: 0, x: -20 })
    gsap.set(ragxRef.current, { y: 100, opacity: 0 })
    gsap.set(askBarRef.current, { opacity: 0, y: 20 })
    gsap.set(buttonsRef.current, { opacity: 0, y: 20 })

    tl.to(ragxRef.current, {
      y: 0,
      opacity: 1,
      duration: 1,
      delay: 0.5,
    })
      .to(
        sahrdayaRef.current,
        {
          opacity: 1,
          x: 0,
          duration: 0.8,
          width: "auto",
          marginRight: "1rem",
        },
        "+=0.2",
      )
      .to(
        askBarRef.current,
        {
          opacity: 1,
          y: 0,
          duration: 0.8,
        },
        "-=0.3",
      )
      .to(
        buttonsRef.current,
        {
          opacity: 1,
          y: 0,
          duration: 1,
        },
        "-=0.5",
      )

    return () => {
      tl.kill()
    }
  }, [])

  // Cycle through placeholder questions
  useEffect(() => {
    let index = 0
    const interval = setInterval(() => {
      index = (index + 1) % QUESTIONS.length
      setPlaceholder(QUESTIONS[index])
    }, 3000)

    return () => clearInterval(interval)
  }, [])

  return (
    <div className="flex flex-col items-center gap-16">
      <div
        ref={containerRef}
        className="flex items-center justify-center text-5xl md:text-8xl tracking-tighter md:tracking-tight text-primary font-nerd"
      >
        <span ref={sahrdayaRef} className="overflow-hidden whitespace-nowrap inline-block" style={{ width: 0 }}>
          Sahrdaya
        </span>
        <span ref={ragxRef} className="inline-block">
          RAGx
        </span>
      </div>

      <div ref={askBarRef} className="w-full max-w-3xl">
        <div className="relative">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="w-full pl-6 pr-14 py-6 text-lg bg-white/50 backdrop-blur-sm border-2 border-primary rounded-xl focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-all placeholder:text-muted-foreground/60"
          />
          <button 
            onClick={handleSubmit}
            className="absolute right-3 top-1/2 -translate-y-1/2 p-3 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
          >
            <Send size={20} />
          </button>
        </div>
      </div>

      <div ref={buttonsRef} className="flex gap-6">
        <Button
          variant="outline"
          className="border-primary text-primary hover:bg-black hover:text-white hover:border-black transition-all duration-300 text-sm py-3 px-8 h-auto uppercase bg-transparent font-semibold"
        >
          Get Started
        </Button>
        <Button
          variant="outline"
          className="border-primary text-primary hover:bg-black hover:text-white hover:border-black transition-all duration-300 text-sm py-3 px-8 h-auto uppercase bg-transparent font-semibold"
        >
          Sign In
        </Button>
      </div>
    </div>
  )
}
