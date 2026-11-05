/** Images in a card's description, held in the blob store and referred to by URL. */

import { request } from './http'

/** An uploaded image. `url` is what a description's markdown points at. */
export interface DescriptionImage {
  id: string
  /** `/api/v1/images/{id}`, from the site root — see `imageSrc` for drawing it. */
  url: string
  mime: string
  size: number
}

export const imagesApi = {
  /** Filed under whoever is signed in: the server takes the uploader from the session. */
  uploadImage: (projectKey: string, file: File) => {
    const form = new FormData()
    form.append('file', file)
    return request<DescriptionImage>(`/projects/${projectKey}/images`, {
      method: 'POST',
      body: form,
    })
  },
}
