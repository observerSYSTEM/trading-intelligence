"use client";

import { useEffect } from "react";

export default function RootLayoutLogger() {
  useEffect(() => {
    if (process.env.NODE_ENV === "production") return;
    console.log("[layout] public root layout");
  }, []);

  return null;
}
