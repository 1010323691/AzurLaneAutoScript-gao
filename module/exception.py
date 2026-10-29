class CampaignEnd(Exception):
    pass


class CampaignDefeat(CampaignEnd):
    # Fleet defeated in campaign, exp info C or D.
    # Inherits CampaignEnd so that callers who don't handle defeat still end the campaign.
    pass


class OilExhausted(Exception):
    pass


class OilMaxed(Exception):
    pass


class MapDetectionError(Exception):
    pass


class MapWalkError(Exception):
    pass


class MapEnemyMoved(Exception):
    pass


class CampaignNameError(Exception):
    pass


class ScriptError(Exception):
    # This is likely to be a mistake of developers, but sometimes a random issue
    pass


class ScriptEnd(Exception):
    pass


class GameStuckError(Exception):
    pass


class GameBugError(Exception):
    # An error has occurred in Azur Lane game client. Alas is unable to handle.
    # A restart should fix it.
    pass


class GameTooManyClickError(Exception):
    pass


class EmulatorNotRunningError(Exception):
    pass


class GameNotRunningError(Exception):
    pass


class GamePageUnknownError(Exception):
    pass


class RequestHumanTakeover(Exception):
    # Request human takeover
    # Alas is unable to handle such error, probably because of wrong settings.
    pass


class TaskRequirementNotMet(RequestHumanTakeover):
    # In-game requirements of current task are not met,
    # such as commander level, stage progress, stage stats, fleet preparation.
    # Current task can be skipped if Restart.StuckHandle.Mode is skip_task,
    # otherwise it's handled as RequestHumanTakeover.
    pass


class HardNotSatisfied(TaskRequirementNotMet):
    pass
