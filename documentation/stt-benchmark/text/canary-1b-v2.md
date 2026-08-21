# canary-1b-v2

| | |
|---|---|
| модель | `nvidia/canary-1b-v2` |
| время расшифровки | 367.98 с |
| пик VRAM | 14135 МБ |
| сегментов | 165 |
| пословных таймингов | 2950 |

---

**00:00**  represent per page.

**00:02**  So like each string represents whatever is on an entire page.

**00:07**  And then the last page that the user looked at, this would just be some integer and this would be an index into this list of strings.

**00:14**  So I would have to remember that this, you know, there might be an off by one error here.

**00:24**  because in Python we start at zero, but like naturally when we're reading a book we might start at one.

**00:29**  Yep.

**00:31**  Now for this library, for this collection of books, okay, well, so for this active book, you could either use a Boolean where you set one book to active and the rest to inactive, but I think that that would be kind of excessive because every single time we toggle a book as active, we have to toggle everything else as inactive, right?

**00:54**  You can't have two active books.

**00:56**  Yeah, yeah.

**00:58**  Okay.

**00:58**  Yeah, that makes sense.

**01:00**  Okay.

**01:00**  Okay, so then I think in that case instead what I would rather do is have some sort of a string or some sort of like an ID matching system where we have like one variable that's set to the active book and that might be the title or the ID of a certain book in the library.

**01:24**  So maybe we can just add ID here.

**01:29**  And maybe I can make that.

**01:31**  also a string or an integer or something.

**01:34**  And I'm going to add a question mark to kind of just say like, maybe we could implement that, but I'm not sure if we'd want to right now.

**01:43**  So this would correspond to ID.

**01:48**  So this would just be some variable.

**01:56**  And then this collection of books.

**02:01**  I guess like when we display this page in an active book, I actually, so I think that this might have an advantage being like some sort of a lookup table because we have a certain sense of an ID, right?

**02:22**  And I think what could make sense here is having the ID correspond to the book object that we define up here.

**02:29**  So this could This could be a book object, right?

**02:32**  And so we don't really need this ID.

**02:35**  I think that if all the titles are unique, then we don't need this ID.

**02:41**  We can just use the titles as the IDs.

**02:44**  But in some cases in the real world, not all titles are unique.

**02:48**  And so that's when an ID might come in handy.

**02:53**  And so I'll leave that like...

**02:56**  What are our assumptions here?

**02:58**  Can we assume that the titles are unique or should I be using this ID structure?

**03:02**  I think I like the ID structure because yeah, we're not necessarily going to know that everything's unique, so I think this is a little bit more robust.

**03:10**  Okay, awesome.

**03:12**  So I think this representation looks good to me, so I'm just going to go through the requirements again just to make sure that I've covered all my bases.

**03:21**  So here I want all books.

**03:24**  Okay, so I want a library of books that I can add to or remove from.

**03:28**  And so here I have a library and anytime I want to add to this library, I can just add an ID, I can add the book.

**03:35**  If I want to remove from the library, then I can just simply delete the key item pair or the key, whatever pair, key value pair.

**03:48**  I can set a book from their library as active and so here I have this active book and we could always say like if there is no active book then we set this to none or something like that.

**04:01**  The reading application remembers where a user left off.

**04:04**  So we have that here, right?

**04:06**  The last page that the user looked at and not just in the active book.

**04:10**  And then displaying one page of text at a time in the active book.

**04:15**  So we have the active book.

**04:17**  We can get the book object from that.

**04:19**  And then we can go to the page that the user last looked at by indexing this value into this list.

**04:28**  So I think we've covered all the basics here.

**04:31**  Cool.

**04:32**  Let's do a couple things.

**04:35**  I guess I think as a starting point, and this doesn't have to get too crazy, but I would love to just see some, you know, Python pseudocode or even some Python kind of code just to like maybe flesh out one of these classes.

**04:46**  I just want to, you know, look at that a little bit and how you would actually start writing this code.

**04:52**  Yeah, sure.

**04:54**  So for example, here I let's start with the book.

**04:59**  And when we define a class, let's initialize this.

**05:05**  So when we initialize a book, we definitely should pass in the title and then the content, right?

**05:13**  I'm assuming that when we add a book, these things have to be given to us.

**05:17**  And then I'm assuming that when we add a book, we don't have a last page that the user has looked at.

**05:24**  Is that an okay assumption?

**05:26**  Yeah, I like that assumption.

**05:27**  Okay.

**05:29**  So we can set the title of this book just equal to whatever the title is, the content of this book.

**05:37**  And I'm going to assume that these are given to us in the formatting that we want.

**05:42**  So the title is a string and the content is a list of strings that correspond to the pages.

**05:48**  And I'm going to create a variable last page and I'm going to set that equal to just zero.

**05:54**  I could also probably set it equal to negative one or something, but I think zero makes sense because that would be the beginning, right?

**06:00**  Yep.

**06:04**  And then we need to come up with some sort of an ID.

**06:06**  So typically, maybe we would have some sort of function that takes in the title and the content, or maybe the author or something, and returns a unique ID.

**06:19**  But I think maybe something that we can do here just very simply is have some sort of counter in the library, and then every single time we like add a new book we can just associate that counter with that book and so then we know that they're all unique and and they all have their own IDs would that make sense yeah that that makes sense okay so then I would have to pass in the ID yeah and so what I'm actually going to do self.id equals ID and then yeah and so then, for example, we might want like a display page right in this book.

**07:06**  And so for that, then we just want to return whatever is at the last page of the content.

**07:18**  And I guess let's build on this a little bit.

**07:20**  Another useful function, you know, obviously you want to start at the last page, but let's say we're starting to turn the page.

**07:26**  What might that look like?

**07:29**  Right, and so...

**07:30**  And so if we're turning the page, then all we want to do is increment the last page, right?

**07:39**  And so we might do self.lastpage, and we just increment that by one.

**07:46**  And then we could call display page after that.

**07:51**  If we assume that...

**07:54**  Like we could if we assume that these two go together.

**07:58**  So if we assume that when we turn the page we want to display that page then I would just return display page yep like that and then actually I'm just going to go ahead and code up this library as well so this library we have this collection of books and then also the active book so Rather than initializing the library with books, I think let's just add them to this.

**08:33**  So initially, this collection might just be an empty dictionary and then the active book might be none.

**08:44**  So then when we want to add stuff, so add to collection, book, What I'm going to do so there's either the user could like or our API could like return a book with the with the title and the content etc but since we're we said that we would pass the ID through like the library because we might have like a counter Like some sort of ID counter.

**09:24**  I mean, this is not the best way to do it, but I think this is like a fine hack for right now.

**09:31**  just to make all the IDs unique.

**09:34**  What we can do here is we can also when we want to add a book just pass in the title and the content and so then our new book is going to be a book as we've defined above with the ID and the title and the content.

**09:59**  And then after we create this new book we want to increment the ID counter and then we also want to add this new book into the collection right so I'm going to add self.collection and I'm going to make the ID whatever the ID of the book is so actually I'm just going to call newbook.id because I think that's a bit cleaner and this is going to be the new book and then we increment the counter by one so this is us adding to the collection um so of course we want to remove from the collection and so we want we said that we wanted to remove based on the id so all we have to do is i think there's this delete in python i'm not sure if there's a remove do you know keith okay Is there a remove?

**11:02**  I think that there is, I mean, it should autocomplete here in the coder pad.

**11:06**  Oh, isn't it Dell?

**11:08**  Isn't there a Dell?

**11:11**  I mean, I think the biggest thing is, is it going to be happening in place or is it going to be happening in creating a copy?

**11:18**  I understand.

**11:20**  Yeah, so we want it in place for the collection.

**11:22**  I understand what you're trying to do, so the exact details is not super, super important to me.

**11:29**  Okay.

**11:29**  Yeah, so anyways, this is removedoving from the collection.

**11:34**  And when we set active book, again, we want to do this based on the ID.

**11:40**  So I'm going to make self.active book equal to whatever that ID is.

**11:53**  Let's see.

**11:53**  So the user has a library of books they can add or remove from.

**11:57**  We did that.

**11:58**  Setting a book as active.

**11:59**  Okay.

**12:00**  And then remember.ing where user left off.

**12:02**  So we've actually taken care of that already implicitly in our book class.

**12:08**  And then the reading application only displays a page of text at a time.

**12:12**  All right, so then here we can say define display page.

**12:18**  And actually we don't even need the the ID of the book because we already have the active book ID, right?

**12:25**  And so what I'm going to do is I'm going gonna get the active book like this.

**12:35**  So...

**12:38**  Yep.

**12:39**  Blah, blah.

**12:40**  And I'm gonna click or I'm gonna do dot display.

**12:44**  Okay.

**12:48**  And then of course we also have this turn page that I could just put up as well.

**12:58**  Yep.

**13:00**  So you get the idea, but...

**13:07**  So we want to...

**13:08**  Oops.

**13:09**  Turn that page.

**13:11**  I do think that some drawbacks here are that...

**13:17**  Okay, so one thing that we could have done is instead of...

**13:21**  this active book instead of saving the id we could have saved the book itself and i think from a pythonic standpoint that might make sense but then in the future if we're using some sort of database or something, an ID would make more sense because that's easier to store in like a table.

**13:38**  And if, and okay, also for this display page, you might see that we've called this display page and turn page up here in the book.

**13:48**  And the reason why I did that, rather than, rather than just call like self.book.content last page, like book.last page or something, was because I think that like for example this should be independent of how we implement the book right so we want to keep those as kind of block like separate blocks as possible and so here what this allows me to do is when I go back to my book like if I decide to scrap this implementation implement something else I know that I would just have to implement the methods display page and turn page for that to work and like these functions could work.

**14:32**  This looks good to me.

**14:33**  I like the start.

**14:34**  I think that this definitely lays out the foundation of what I was looking for with the requirements of this application.

**14:41**  So nice work with this.

**14:43**  A couple of quick follow-up questions before we kind of branch into more of an algorithms type question as the follow-up here.

**14:52**  One question is, let's say you have an older reader and their vision isn't as great, so they need to make the size of the file.

**15:00**  the size of the font bigger.

**15:02**  So kind of keeping that in mind that you might have this variable font size, how might you, I guess, refactor or change or modify your current structure?

**15:11**  And you don't have to actually write code here.

**15:12**  Let's just discuss this to account for that increase in size.

**15:20**  Yeah.

**15:21**  Yeah.

**15:23**  So when we increase something in size, like I'm just thinking about intuitively on our phone screens or something, that tends to shift the content, right?

**15:32**  So like some page that might hold 100 words or 100 characters at, you know, a small font size might only hold 50 if one doubled that font size.

**15:44**  So for the book, instead of having this list, what I might do is I might just save the entire book as a string.

**15:57**  And based, so I would, let me just edit this in here because I think it's easier to visualize what I'm saying but so I would might I might have like a font size equal to I guess we could just use like the traditional like 12 point font or like whatever but when we have this font size we could come up with some sort of characters per page calculation Right, and so that would be based off the font size.

**16:29**  So calculate so this is going to be pseudocode now this calculate doesn't actually exist but calculate based on that font size and I'm commenting it out so it doesn't have any errors but then when we do calculate that then what we can do in order to get our in order to get this display page thing would be instead of indexing into this content which is now just a long string of characters what we can do here is we can return we would have to do some sort of calculation right so we would need to we have this characters per page and we can multiply that by whatever the last page is and So this would give us like kind of the starting index of this long string of characters to start.

**17:32**  the content of that page.

**17:34**  Start index would be this.

**17:37**  And then what we would want to do is what we want to do is then return the self.content from that start index until the start index plus the number of characters on that page.

**17:57**  So that would be our end index.

**17:58**  So actually let me just write that out to make things a little bit more clear.

**18:08**  So now essentially what I've done is we have this variable font size which we can calculate characters per page depending on that font size.

**18:16**  So that might be more characters or less characters depending on how big the font size is.

**18:21**  And then our content instead of a list of strings is now just a really long string of characters.

**18:27**  And I guess some assumptions that I'm making here are that this This would just be approximate.

**18:32**  So for example, if we had a backslash N, which is like a line break, right?

**18:38**  That might just end up counting as a character or two characters.

**18:41**  But the assumption is that that would be approximate per page, or maybe that we're using some sort of like monospaced font or something like that.

**18:52**  But essentially...

**18:54**  Now that we have the characters per page and then just a long string of characters that represents the content, we can index into that content depending on like based on the last page that we know.

**19:06**  And so what's really cool here is that when we do turn the page, we can just end up calling this.

**19:11**  So this is kind of that granularity that I was talking about.

**19:14**  Yeah, that makes a lot of sense to me and definitely you see the advantage of having that function that you can change by having to implement something different.

**19:24**  One last quick, I guess, follow up and then we'll move on to the algorithms portion of this interview.

**19:30**  I'm just trying to think, at a high level, let's say that we wanted we had multiple users in this system and they all shared books.

**19:40**  They might all share the Harry Potter books or something like that.

**19:42**  Books are common among all of them.

**19:44**  How might you modify this given that some of these qualities of a book might be shared between users because they're all trying to access that same book?

**19:54**  Does that make sense?

**19:56**  Yeah, so yeah, yeah, for sure.
