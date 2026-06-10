import { useEffect, useRef, useState } from "react";
import { DEFAULT_API_CONFIG, loadApiConfig } from "../api/config";
import type { ApiRuntimeConfig } from "../types/api";

export function useApiConfig() {
  const [apiConfig, setApiConfig] = useState<ApiRuntimeConfig>(DEFAULT_API_CONFIG);
  const [isConfigReady, setIsConfigReady] = useState(false);
  const apiConfigRef = useRef<ApiRuntimeConfig>(DEFAULT_API_CONFIG);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      const config = await loadApiConfig();
      if (cancelled) {
        return;
      }

      apiConfigRef.current = config;
      setApiConfig(config);
      setIsConfigReady(true);
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  return {
    apiConfig,
    apiConfigRef,
    isConfigReady,
  };
}
