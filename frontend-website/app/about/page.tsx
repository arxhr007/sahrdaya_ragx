import Link from "next/link"
import { AnimatedGrid } from "@/components/animated-grid"
import { Button } from "@/components/ui/button"
import { ArrowLeft, ExternalLink, MessageSquare } from "lucide-react"

export default function AboutPage() {
  return (
    <main className="relative min-h-[100dvh] overflow-hidden p-3 md:p-8">
      <AnimatedGrid />

      <div className="relative z-10 mx-auto max-w-4xl pt-4 md:pt-10 pb-6">
        <div className="mb-6 flex flex-wrap items-center gap-3">
          <Link href="/">
            <Button variant="ghost" className="text-primary hover:bg-primary/10">
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back
            </Button>
          </Link>
          <Link href="/chat">
            <Button variant="outline" className="border-primary text-primary hover:bg-primary hover:text-primary-foreground">
              <MessageSquare className="mr-2 h-4 w-4" />
              Open Chat
            </Button>
          </Link>
        </div>

        <section className="rounded-2xl border bg-white/70 p-4 sm:p-6 shadow-sm backdrop-blur-sm md:p-10">
          <p className="mb-2 text-xs uppercase tracking-[0.22em] text-primary/80">About This Project</p>
          <h1 className="mb-4 text-2xl sm:text-3xl font-bold tracking-tight text-foreground md:text-5xl">Sahrdaya RAGx</h1>

          <div className="space-y-4 text-sm leading-7 text-muted-foreground md:text-base">
            <p>
              This project is an AI-powered college assistant built for Sahrdaya College of Engineering and Technology.
              It helps students, parents, and faculty get accurate answers quickly from official college information.
            </p>
            <p>
              The system combines retrieval-augmented generation (RAG), SQL lookups, and curated student/faculty data.
              Instead of searching through long PDFs and scattered pages, users can ask natural questions and get clear,
              structured responses.
            </p>
            <p>
              Core project knowledge is scraped from the official Sahrdaya website and then cleaned, indexed, and served
              through this assistant.
            </p>
            <p>
              Student profile data is collected separately through a Google Form during testing so students can contribute
              their latest profile details (interests, links, bio) for inclusion after review.
            </p>
          </div>

          <div className="mt-8 flex flex-wrap items-center gap-3">
            <a
              href="https://github.com/arxhr007/sahrdaya_ragx"
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button className="bg-primary text-primary-foreground hover:bg-primary/90">
                View Project Repository
                <ExternalLink className="ml-2 h-4 w-4" />
              </Button>
            </a>
            <a
              href="https://forms.gle/Ts644nDzj1F9nMmN9"
              target="_blank"
              rel="noopener noreferrer"
            >
              <Button variant="outline" className="border-primary text-primary hover:bg-primary hover:text-primary-foreground">
                Fill Student Data Form
                <ExternalLink className="ml-2 h-4 w-4" />
              </Button>
            </a>
          </div>

          <p className="mt-4 text-xs text-muted-foreground">
            Why fill the form: it helps keep student details current and improves answer quality for student-related queries.
          </p>

          <p className="mt-2 text-xs text-muted-foreground">
            This project is currently in test phase (v1). Please read our{" "}
            <Link href="/terms" className="text-primary underline underline-offset-4 hover:text-primary/80">
              Terms & Conditions
            </Link>{" "}
            before using the assistant for important decisions.
          </p>
        </section>
      </div>
    </main>
  )
}
