import { Html, Head, Main, NextScript } from 'next/document';

/** Idioma do documento (leitores de tela) — padrão comum ao painel Vite (index.html lang="pt-BR"). */
export default function Document() {
  return (
    <Html lang="pt-BR">
      <Head>
        <link rel="icon" type="image/svg+xml" href="/marca-ctrl-fiscal.svg" />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
