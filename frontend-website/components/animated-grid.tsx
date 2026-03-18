"use client"

import { useEffect, useRef, useState, useCallback } from "react"
import gsap from "gsap"

const CELL_SIZE = 40
const FADE_DURATION = 1200 // ms for trail to fade out - longer for smoother trails
const FLIP_DURATION = 400 // ms for flip animation - slightly slower for elegance

interface CellState {
  x: number
  y: number
  opacity: number
  timestamp: number
  // flip animation
  flipTimestamp?: number
  flipProgress?: number // 0 -> 1
}

export function AnimatedGrid() {
  const gridRef = useRef<HTMLDivElement>(null)
  const [currentCell, setCurrentCell] = useState({ x: -1, y: -1, timestamp: 0 })
  const [visible, setVisible] = useState(false)
  const [offset, setOffset] = useState(0)
  const [trailCells, setTrailCells] = useState<Map<string, CellState>>(new Map())
  const mousePos = useRef({ x: 0, y: 0 })
  const frameRef = useRef<number | undefined>(undefined)

  const calculateCell = useCallback((clientX: number, clientY: number, currentOffset: number) => {
    const adjustedX = clientX - currentOffset
    const adjustedY = clientY - currentOffset
    
    const x = Math.floor(adjustedX / CELL_SIZE)
    const y = Math.floor(adjustedY / CELL_SIZE)
    
    return { x, y }
  }, [])

  // Update trail cells opacity over time
  useEffect(() => {
    const updateTrail = () => {
      const now = Date.now()
      
      setTrailCells(prev => {
        const newMap = new Map(prev)
        let hasChanges = false
        
        for (const [key, cell] of newMap) {
          const elapsed = now - cell.timestamp
          const newOpacity = Math.max(0, 1 - (elapsed / FADE_DURATION))

          // update flip progress if flipTimestamp exists
          let newFlipProgress = cell.flipProgress ?? 0
          if (cell.flipTimestamp) {
            const flipElapsed = now - cell.flipTimestamp
            newFlipProgress = Math.min(1, flipElapsed / FLIP_DURATION)
          }
          
          if (newOpacity <= 0) {
            newMap.delete(key)
            hasChanges = true
          } else {
            // Only set when opacity or flip progress changed enough
            if (Math.abs(newOpacity - cell.opacity) > 0.01 || Math.abs((newFlipProgress) - (cell.flipProgress ?? 0)) > 0.01) {
              newMap.set(key, { ...cell, opacity: newOpacity, flipProgress: newFlipProgress })
              hasChanges = true
            }
          }
        }
        
        return hasChanges ? newMap : prev
      })
      
      frameRef.current = requestAnimationFrame(updateTrail)
    }
    
    frameRef.current = requestAnimationFrame(updateTrail)
    
    return () => {
      if (frameRef.current) {
        cancelAnimationFrame(frameRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!gridRef.current) return

    const tween = gsap.to({ value: 0 }, {
      value: CELL_SIZE,
      duration: 2,
      repeat: -1,
      ease: "none",
      onUpdate: function() {
        const currentOffset = this.targets()[0].value
        setOffset(currentOffset)
        
        if (gridRef.current) {
          gridRef.current.style.backgroundPosition = `${currentOffset}px ${currentOffset}px`
        }

        if (mousePos.current.x !== 0 || mousePos.current.y !== 0) {
          const cell = calculateCell(mousePos.current.x, mousePos.current.y, currentOffset)
          setCurrentCell({ ...cell, timestamp: Date.now() })
        }
      }
    })

    return () => {
      tween.kill()
    }
  }, [calculateCell])

  const addToTrail = useCallback((x: number, y: number) => {
    const key = `${x},${y}`
    const now = Date.now()
    setTrailCells(prev => {
      const newMap = new Map(prev)
      // start flip animation when adding to trail
      newMap.set(key, { x, y, opacity: 1, timestamp: now, flipTimestamp: now, flipProgress: 0 })
      return newMap
    })
  }, [])

  // Keep a ref of the current cell so global handlers can access latest value
  const currentCellRef = useRef({ x: -1, y: -1 })

  useEffect(() => {
    currentCellRef.current = currentCell
  }, [currentCell])

  // Global mouse handlers so the grid can track the cursor even when it's behind interactive UI
  useEffect(() => {
    const handleGlobalMove = (e: MouseEvent) => {
      mousePos.current = { x: e.clientX, y: e.clientY }
      const cell = calculateCell(e.clientX, e.clientY, offset)

      const prev = currentCellRef.current
      // Add previous cell to trail if it changed
      if (prev.x !== cell.x || prev.y !== cell.y) {
        if (prev.x !== -1) {
          addToTrail(prev.x, prev.y)
        }
      }

      setCurrentCell({ x: cell.x, y: cell.y, timestamp: Date.now() })
      setVisible(true)
    }

    const handleGlobalLeave = () => {
      const prev = currentCellRef.current
      if (prev.x !== -1) {
        addToTrail(prev.x, prev.y)
      }
      setVisible(false)
      mousePos.current = { x: 0, y: 0 }
      setCurrentCell({ x: -1, y: -1, timestamp: 0 })
    }

    const onMouseOut = (e: MouseEvent) => {
      // When leaving the window, relatedTarget is null
      // 'mouseleave' doesn't always fire on some browsers when leaving the window, so handle both
      if (!(e as any).relatedTarget) {
        handleGlobalLeave()
      }
    }

    window.addEventListener("mousemove", handleGlobalMove)
    window.addEventListener("mouseout", onMouseOut)
    window.addEventListener("mouseleave", handleGlobalLeave)

    return () => {
      window.removeEventListener("mousemove", handleGlobalMove)
      window.removeEventListener("mouseout", onMouseOut)
      window.removeEventListener("mouseleave", handleGlobalLeave)
    }
  }, [calculateCell, offset, addToTrail])

  return (
    <div
      className="fixed inset-0 z-0 pointer-events-none"
    >
      {/* GRID */}
      <div
        ref={gridRef}
        className="absolute inset-0 pointer-events-none opacity-40"
        style={{
          backgroundImage: `
            linear-gradient(to right, var(--grid-color, #82CCFA) 1px, transparent 1px),
            linear-gradient(to bottom, var(--grid-color, #82CCFA) 1px, transparent 1px)
          `,
          backgroundSize: "40px 40px",
        }}
      />

      {/* TRAIL CELLS - pop out then flip then back, fading */}
      {Array.from(trailCells.values()).map((cell) => {
        const flip = cell.flipProgress ?? 0
        const rotation = flip * 180
        // pop out and back: peaks at flip=0.5, starts and ends at 0
        const pop = Math.sin(flip * Math.PI)
        
        // Proximity-based flip: tiles near cursor flip based on distance
        const tileX = cell.x * CELL_SIZE + offset + CELL_SIZE / 2
        const tileY = cell.y * CELL_SIZE + offset + CELL_SIZE / 2
        const cursorX = mousePos.current.x
        const cursorY = mousePos.current.y
        const distX = tileX - cursorX
        const distY = tileY - cursorY
        const distance = Math.sqrt(distX * distX + distY * distY)
        
        // Proximity flip: tiles within ~80px of cursor get semi-flip effect
        const maxProximityDistance = 80
        const proximityFlip = Math.max(0, 1 - (distance / maxProximityDistance)) * 0.5
        
        const totalFlip = flip + proximityFlip
        const totalRotation = totalFlip * 180
        const scale = 1 + 0.4 * pop
        const translateZ = 20 * pop
        const shadowOpacity = 0.3 * (cell.opacity ?? 1) * pop
        const glowIntensity = pop * 0.6
        // Alternate colors: use dark blue for some tiles based on position
        const isDark = (cell.x + cell.y) % 3 === 0
        const tileColor = isDark ? 'rgba(30, 64, 175, 0.9)' : 'var(--tile-front, #82CCFA)'
        const glowColor = isDark ? 'rgba(30, 64, 175, 0.5)' : 'rgba(130, 204, 250, 0.5)'

        return (
          <div
            key={`${cell.x},${cell.y}`}
            className="absolute pointer-events-none"
            style={{
              width: CELL_SIZE,
              height: CELL_SIZE,
              left: cell.x * CELL_SIZE + offset,
              top: cell.y * CELL_SIZE + offset,
              opacity: cell.opacity * 0.7,
              transformStyle: 'preserve-3d',
              transform: `perspective(800px) translateZ(${translateZ}px) rotateY(${totalRotation}deg) scale(${scale})`,
              transition: 'transform 0s',
              boxShadow: `0 ${12 * pop}px ${28 * pop}px rgba(0,0,0,${shadowOpacity}), 0 0 ${20 * glowIntensity}px ${glowColor}`,
            }}
          >
            {/* front face */}
            <div style={{
              position: 'absolute',
              inset: 0,
              backfaceVisibility: 'hidden',
              WebkitBackfaceVisibility: 'hidden',
              borderRadius: 6,
              backgroundColor: tileColor,
              boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.2)',
            }} />

            {/* back face (dark blue) */}
            <div style={{
              position: 'absolute',
              inset: 0,
              transform: 'rotateY(180deg)',
              backfaceVisibility: 'hidden',
              WebkitBackfaceVisibility: 'hidden',
              borderRadius: 6,
              backgroundColor: 'var(--tile-back, #1e40af)',
              boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.1)',
            }} />
          </div>
        )
      })}

      {/* CURRENT HOVER CELL - pop out, flip, and fade */}
      {visible && currentCell.x !== -1 && (
        (() => {
          const now = Date.now()
          const elapsed = now - currentCell.timestamp
          const flipProgress = Math.min(1, elapsed / FLIP_DURATION)
          const rotation = flipProgress * 180
          const pop = Math.sin(flipProgress * Math.PI)
          const scale = 1 + 0.5 * pop
          const translateZ = 25 * pop
          const shadowOpacity = 0.3 * pop
          const glowIntensity = 0.8
          // Alternate colors: use dark blue for some tiles
          const isDark = (currentCell.x + currentCell.y) % 3 === 0
          const tileColor = isDark ? 'rgba(30, 64, 175, 0.95)' : 'var(--tile-front, #82CCFA)'
          const glowColor = isDark ? 'rgba(30, 64, 175, 0.7)' : 'rgba(130, 204, 250, 0.7)'

          return (
            <div
              className="absolute pointer-events-none"
              style={{
                width: CELL_SIZE,
                height: CELL_SIZE,
                left: currentCell.x * CELL_SIZE + offset,
                top: currentCell.y * CELL_SIZE + offset,
                opacity: 1,
                transformStyle: 'preserve-3d',
                transform: `perspective(800px) translateZ(${translateZ}px) rotateY(${rotation}deg) scale(${scale})`,
                transition: 'transform 0s',
                boxShadow: `0 ${12 * pop}px ${28 * pop}px rgba(0,0,0,${shadowOpacity}), 0 0 ${30 * glowIntensity}px ${glowColor}`,
              }}
            >
              {/* front face */}
              <div style={{
                position: 'absolute',
                inset: 0,
                backfaceVisibility: 'hidden',
                WebkitBackfaceVisibility: 'hidden',
                borderRadius: 6,
                backgroundColor: tileColor,
                boxShadow: 'inset 0 2px 0 rgba(255,255,255,0.3)',
              }} />

              {/* back face (dark blue) */}
              <div style={{
                position: 'absolute',
                inset: 0,
                transform: 'rotateY(180deg)',
                backfaceVisibility: 'hidden',
                WebkitBackfaceVisibility: 'hidden',
                borderRadius: 6,
                backgroundColor: 'var(--tile-back, #1e40af)',
                boxShadow: 'inset 0 2px 0 rgba(255,255,255,0.15)',
              }} />
            </div>
          )
        })()
      )}

      {/* PROXIMITY TILES - semi-flip tiles near cursor for smooth key effect */}
      {visible && currentCell.x !== -1 && (() => {
        const proximityRadius = 4 // number of cells around cursor
        const proximityTiles: React.ReactNode[] = []
        const cursorX = mousePos.current.x
        const cursorY = mousePos.current.y

        for (let dx = -proximityRadius; dx <= proximityRadius; dx++) {
          for (let dy = -proximityRadius; dy <= proximityRadius; dy++) {
            if (dx === 0 && dy === 0) continue // skip cursor cell itself
            
            const cellX = currentCell.x + dx
            const cellY = currentCell.y + dy
            const key = `${cellX},${cellY}`
            
            // skip if this cell is in the trail
            if (trailCells.has(key)) continue

            // Calculate distance from tile center to cursor
            const tileX = cellX * CELL_SIZE + offset + CELL_SIZE / 2
            const tileY = cellY * CELL_SIZE + offset + CELL_SIZE / 2
            const distX = tileX - cursorX
            const distY = tileY - cursorY
            const distance = Math.sqrt(distX * distX + distY * distY)

            // Max distance for effect (in pixels)
            const maxDist = proximityRadius * CELL_SIZE
            if (distance > maxDist) continue

            // Smooth falloff: closer = more flip
            const proximity = 1 - (distance / maxDist)
            const flipAmount = proximity * 0.4 // max 40% flip for nearby tiles
            const rotation = flipAmount * 180
            const pop = Math.sin(flipAmount * Math.PI)
            const scale = 1 + 0.25 * pop
            const translateZ = 12 * pop
            const opacity = proximity * 0.5
            const glowIntensity = proximity * 0.4
            // Alternate colors for proximity tiles
            const isDark = (cellX + cellY) % 3 === 0
            const tileColor = isDark ? 'rgba(30, 64, 175, 0.8)' : 'var(--tile-front, #82CCFA)'
            const glowColor = isDark ? 'rgba(30, 64, 175, 0.4)' : 'rgba(130, 204, 250, 0.4)'

            proximityTiles.push(
              <div
                key={key}
                className="absolute pointer-events-none"
                style={{
                  width: CELL_SIZE,
                  height: CELL_SIZE,
                  left: cellX * CELL_SIZE + offset,
                  top: cellY * CELL_SIZE + offset,
                  opacity,
                  transformStyle: 'preserve-3d',
                  transform: `perspective(800px) translateZ(${translateZ}px) rotateY(${rotation}deg) scale(${scale})`,
                  transition: 'transform 0.08s ease-out, opacity 0.08s ease-out',
                  boxShadow: `0 ${6 * pop}px ${16 * pop}px rgba(0,0,0,${0.15 * pop}), 0 0 ${15 * glowIntensity}px ${glowColor}`,
                }}
              >
                {/* front face */}
                <div style={{
                  position: 'absolute',
                  inset: 0,
                  backfaceVisibility: 'hidden',
                  WebkitBackfaceVisibility: 'hidden',
                  borderRadius: 6,
                  backgroundColor: tileColor,
                  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.15)',
                }} />

                {/* back face */}
                <div style={{
                  position: 'absolute',
                  inset: 0,
                  transform: 'rotateY(180deg)',
                  backfaceVisibility: 'hidden',
                  WebkitBackfaceVisibility: 'hidden',
                  borderRadius: 6,
                  backgroundColor: 'var(--tile-back, #1e40af)',
                  boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.08)',
                }} />
              </div>
            )
          }
        }

        return proximityTiles
      })()}
    </div>
  )
}
