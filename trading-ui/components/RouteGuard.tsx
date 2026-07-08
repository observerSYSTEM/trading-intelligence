"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { getToken } from "@/lib/api";

type Props = {
  children: React.ReactNode;
  requireAuth?: boolean; // default true
};

export default function RouteGuard({ children, requireAuth = true }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    const id = window.setTimeout(() => {
      setMounted(true);
    }, 0);

    return () => {
      window.clearTimeout(id);
    };
  }, []);

  useEffect(() => {
    if (!mounted) return;

    const token = getToken();
    if (requireAuth && !token) {
      router.replace(`/login?next=${encodeURIComponent(pathname)}`);
    }
  }, [mounted, requireAuth, router, pathname]);

  if (!mounted) return null;
  if (requireAuth && !getToken()) return null;
  return <>{children}</>;
}
