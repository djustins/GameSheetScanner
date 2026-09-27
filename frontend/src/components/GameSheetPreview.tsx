import { useMemo } from 'react'
import { Box } from '@mantine/core'

export function GameSheetPreview({ file, page }: { file: File; page?: number }) {
  const url = useMemo(() => URL.createObjectURL(file), [file])
  const isPdf = file.type === 'application/pdf'

  return (
    <Box h={700} style={{ border: '1px solid var(--mantine-color-gray-3)', borderRadius: 4, overflow: 'hidden' }}>
      {isPdf ? (
        <embed src={`${url}${page ? `#page=${page}` : ''}`} type="application/pdf" width="100%" height="100%" />
      ) : (
        <img src={url} alt="Game sheet preview" style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
      )}
    </Box>
  )
}
