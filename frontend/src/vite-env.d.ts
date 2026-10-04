/// <reference types="vite/client" />

declare module 'react' {
  // The type parameter is unused here and has to be: an interface only merges
  // with React's own when the two take the same number of them.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  interface InputHTMLAttributes<T> {
    /**
     * Pick a whole directory rather than files — Chrome, Safari and Firefox
     * all honour it, and each chosen `File` then carries the path it had
     * inside the directory as `webkitRelativePath`.
     *
     * Not in React's own typings because it is not in the HTML standard; it
     * is declared here rather than cast at the call site so the one place
     * that uses it reads like every other attribute.
     */
    webkitdirectory?: string
  }
}

export {}
