"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import gsap from "gsap"
import { Button } from "@/components/ui/button"
import { Send } from "lucide-react"

const QUESTIONS = [
  "List all faculty from CSE",
  "Who is Aaron",
  "Show students interested in chess",
  "Who is the HOD of CSE?",
  "How many faculty are in BME department?",
  "Who are the former principals?",
]

export function HeroAnimation({ onAnimationComplete }: { onAnimationComplete?: () => void }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const ragxRef = useRef<HTMLSpanElement>(null)
  const sahrdayaRef = useRef<HTMLSpanElement>(null)
  const askBarRef = useRef<HTMLDivElement>(null)
  const buttonsRef = useRef<HTMLDivElement>(null)
  const messageRef = useRef<HTMLParagraphElement>(null)
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
    gsap.set(messageRef.current, { opacity: 0, y: 20 })

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
      .to(
        messageRef.current,
        {
          opacity: 1,
          y: 0,
          duration: 0.8,
        },
        "-=0.3",
      )

    return () => {
      tl.kill()
    }
  }, [])

  useEffect(() => {
    let index = 0
    const interval = setInterval(() => {
      index = (index + 1) % QUESTIONS.length
      setPlaceholder(QUESTIONS[index])
    }, 3000)

    return () => clearInterval(interval)
  }, [])

  return (
    <div className="flex w-full flex-col items-center gap-8 md:gap-16">
      <div
        ref={containerRef}
        className="flex items-center justify-center text-4xl sm:text-6xl md:text-8xl tracking-tighter md:tracking-tight text-primary font-nerd"
      >
        <span ref={sahrdayaRef} className="overflow-hidden whitespace-nowrap inline-block" style={{ width: 0 }}>
          Sahrdaya
        </span>
        <span ref={ragxRef} className="inline-block">
          RAGx
        </span>
      </div>

      <div ref={askBarRef} className="w-full max-w-3xl px-1">
        <div className="relative">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="w-full pl-4 pr-12 py-4 md:py-6 text-base md:text-lg bg-white/50 backdrop-blur-sm border-2 border-primary rounded-xl focus:outline-none focus:ring-2 focus:ring-primary focus:border-transparent transition-all placeholder:text-muted-foreground/60"
          />
          <button
            onClick={handleSubmit}
            className="absolute right-2 md:right-3 top-1/2 -translate-y-1/2 p-2.5 md:p-3 bg-primary text-white rounded-lg hover:bg-primary/90 transition-colors"
          >
            <Send size={20} />
          </button>
        </div>
      </div>

      <div ref={buttonsRef} className="flex w-full max-w-4xl flex-wrap justify-center gap-3 md:gap-6">
        <Button
          variant="outline"
          onClick={() => router.push("/about")}
          className="w-full sm:w-auto border-primary text-primary hover:bg-black hover:text-white hover:border-black transition-all duration-300 text-xs sm:text-sm py-3 px-5 sm:px-8 h-auto uppercase bg-transparent font-semibold"
        >
          About This Project
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            window.open("https://forms.gle/Ts644nDzj1F9nMmN9", "_blank", "noopener,noreferrer")
          }}
          className="w-full sm:w-auto border-primary text-primary hover:bg-black hover:text-white hover:border-black transition-all duration-300 text-xs sm:text-sm py-3 px-5 sm:px-8 h-auto uppercase bg-transparent font-semibold"
        >
          Student Form
        </Button>
        <Button
          variant="outline"
          onClick={() => {
            window.open("https://github.com/arxhr007/sahrdaya_ragx", "_blank", "noopener,noreferrer")
          }}
          className="w-full sm:w-auto border-primary text-primary hover:bg-black hover:text-white hover:border-black transition-all duration-300 text-xs sm:text-sm py-3 px-5 sm:px-8 h-auto uppercase bg-transparent font-semibold"
        >
          Project Repo
        </Button>
      </div>

      <p ref={messageRef} className="text-xs font-bold text-foreground mt-4">
        Use Sahrdaya mail to submit the form
      </p>
    </div>
  )
}
