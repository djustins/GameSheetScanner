import { useState } from 'react'
import { Avatar, Button, FileButton, Group, Text } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deletePlayerPhoto, fetchPlayerPhoto, uploadPlayerPhoto } from '../api/players'
import { ApiError } from '../api/client'

// Photos are stored small: the longest side is scaled down to this before upload.
const MAX_SIDE = 400

/** Shrinks a chosen image in the browser and re-encodes it as a JPEG, so a
 * multi-megabyte phone photo goes up as a few tens of kilobytes. */
async function shrinkImage(file: File): Promise<Blob> {
  const bitmap = await createImageBitmap(file, { imageOrientation: 'from-image' })
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height))
  const canvas = document.createElement('canvas')
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext('2d')!.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('Could not read that image.'))), 'image/jpeg', 0.85)
  )
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/)
  return ((parts[0]?.[0] ?? '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

/** A player's photo, or their initials when they have none. `version` is the
 * player's photo_version: null means no photo (and no request is made), and a
 * new value after a re-upload fetches the new picture. */
export function PlayerAvatar({
  playerId,
  version,
  name,
  size = 48,
}: {
  playerId: number | null
  version: number | null | undefined
  name: string
  size?: number
}) {
  const { data: url } = useQuery({
    queryKey: ['player-photo', playerId, version],
    // The API needs the sign-in token, which an <img src> can't send.
    queryFn: async () => URL.createObjectURL(await fetchPlayerPhoto(playerId!)),
    enabled: playerId != null && version != null,
    staleTime: Infinity,
  })
  return (
    <Avatar src={version != null ? url : null} alt={name} size={size} radius="xl" color="gold">
      {initials(name)}
    </Avatar>
  )
}

/** The photo with Upload / Replace and Remove, for a player's profile. */
export function PlayerPhotoEditor({
  playerId,
  name,
  version: savedVersion,
  canEdit,
}: {
  playerId: number
  name: string
  version: number | null | undefined
  canEdit: boolean
}) {
  const queryClient = useQueryClient()
  // What this profile shows once a photo has been changed here, ahead of the lists refreshing.
  const [changed, setChanged] = useState<{ version: number | null } | null>(null)
  const version = changed ? changed.version : savedVersion

  const onError = (err: unknown) =>
    notifications.show({
      color: 'red',
      message: err instanceof ApiError || err instanceof Error ? err.message : 'Something went wrong.',
    })
  const refreshLists = () => {
    queryClient.invalidateQueries({ queryKey: ['players'] })
    queryClient.invalidateQueries({ queryKey: ['roster'] })
  }
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => uploadPlayerPhoto(playerId, await shrinkImage(file)),
    onSuccess: (result) => {
      setChanged({ version: result.photo_version })
      refreshLists()
    },
    onError,
  })
  const removeMutation = useMutation({
    mutationFn: () => deletePlayerPhoto(playerId),
    onSuccess: () => {
      setChanged({ version: null })
      refreshLists()
    },
    onError,
  })

  return (
    <Group>
      <PlayerAvatar playerId={playerId} version={version} name={name} size={96} />
      {canEdit && (
        <div>
          <Group gap="xs">
            <FileButton onChange={(file) => file && uploadMutation.mutate(file)} accept="image/*">
              {(props) => (
                <Button {...props} size="xs" variant="default" loading={uploadMutation.isPending}>
                  {version != null ? 'Replace photo' : 'Upload photo'}
                </Button>
              )}
            </FileButton>
            {version != null && (
              <Button
                size="xs"
                variant="subtle"
                color="red"
                loading={removeMutation.isPending}
                onClick={() => confirm(`Remove ${name}'s photo?`) && removeMutation.mutate()}
              >
                Remove
              </Button>
            )}
          </Group>
          <Text size="xs" c="dimmed" mt={4}>
            Any photo works; it&apos;s shrunk before it&apos;s saved. Only people signed in to this app can see it.
          </Text>
        </div>
      )}
    </Group>
  )
}
