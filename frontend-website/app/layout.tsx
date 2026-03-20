import type React from "react"
import type { Metadata } from "next"
import localFont from "next/font/local"
import { Analytics } from "@vercel/analytics/next"
import "./globals.css"

const nerdFont = localFont({
  src: [
    {
      path: "../public/3270/3270NerdFont-Regular.ttf",
      weight: "400",
      style: "normal",
    },
  ],
  variable: "--font-nerd",
})

export const metadata: Metadata = {
  title: "Sahrdaya RagX",
  description: "Rag for Sahrdaya",
  icons: {
    icon: "/logo.png",
    shortcut: "/logo.png",
    apple: "/logo.png",
  },
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en">
      <body className={`antialiased ${nerdFont.variable} font-nerd`}>
        {children}
        <Analytics />
      </body>
    </html>
  )
}
