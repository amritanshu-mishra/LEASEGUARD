import React, { createContext, useContext, useEffect, useState } from 'react'
import { Lang, strings, t as translate, StringKey } from './i18n'

type Ctx = {
  lang: Lang
  setLang: (l: Lang) => void
  t: (k: StringKey, vars?: Record<string, string | number>) => string
}
const LangCtx = createContext<Ctx | null>(null)

const STORAGE_KEY = 'leaseguard.lang'

function initialLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY)
    if (saved === 'en' || saved === 'hi') return saved
  } catch {
    /* private mode */
  }
  return 'en'
}

export function LangProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLang] = useState<Lang>(initialLang)

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, lang)
      document.documentElement.lang = lang
    } catch {
      /* ignore */
    }
  }, [lang])

  const t = (k: StringKey, vars?: Record<string, string | number>) => {
    let out = translate(k, lang)
    if (vars) {
      for (const [name, value] of Object.entries(vars)) {
        out = out.replace(new RegExp(`\\{${name}\\}`, 'g'), String(value))
      }
    }
    return out
  }

  return <LangCtx.Provider value={{ lang, setLang, t }}>{children}</LangCtx.Provider>
}

export function useLang() {
  const ctx = useContext(LangCtx)
  if (!ctx) throw new Error('useLang outside provider')
  return ctx
}
